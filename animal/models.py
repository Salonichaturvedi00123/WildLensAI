from django.db import models

# Create your models here.
class AnimalPrediction(models.Model):
    image = models.ImageField(upload_to='uploads/')
    species = models.CharField(max_length=255)
    confidence = models.FloatField()
    scientific_name = models.CharField(max_length=255)
    credit_used = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.species} ({self.confidence:.2f})"

class Contact(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField()
    subject = models.CharField(max_length=255)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.email})"