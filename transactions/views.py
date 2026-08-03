from django.shortcuts import render,redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from .models import Profile

# Create your views here.
def register(request):
    if request.method == "POST":

        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        username = request.POST.get("username")
        email = request.POST.get("email")
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirm_password")

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return redirect("register")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("register")

        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already exists.")
            return redirect("register")

        user = User.objects.create_user(
            first_name=first_name,
            last_name=last_name,
            username=username,
            email=email,
            password=password,
        )

        Profile.objects.create(
            user=user,
            wallet_balance=5.00     # Free credits on signup
        )

        messages.success(request, "Registration successful. Please login.")
        return redirect("login")

    return render(request, "register.html")


# Login
def user_login(request):

    if request.method == "POST":

        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:
            login(request, user)
            messages.success(request, "Login Successful.")
            return redirect("home")

        else:
            messages.error(request, "Invalid Username or Password.")

    return render(request, "login.html")


# Logout
# def user_logout(request):
#     logout(request)
#     messages.success(request, "Logged Out Successfully.")
#     return redirect("trlogin")


# Profile
def profile(request):

    if not request.user.is_authenticated:
        return redirect("auth.login")

    profile = request.user.profile

    context = {
        "profile": profile
    }

    return render(request, "profile.html", context)

def forgot_password(request):
    return render(request, 'transactions/forgot_password.html')