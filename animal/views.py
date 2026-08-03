from django.shortcuts import render
from django.http import HttpResponse
from .models import AnimalPrediction, Contact
from .utils import predict_animal

# Create your views here.
# def base(request):
#     return render(request, 'base.html')

def home(request):
    return render(request, 'animal/home.html')

def predict(request):
    prediction = None
    if request.method == 'POST':
        image = request.FILES.get('image')
        prediction = predict_animal(image)
        AnimalPrediction.objects.create(
            image=image,
            species=prediction['species'],
            confidence=prediction['confidence'],
            scientific_name=prediction['scientific_name'],
            credit_used=prediction['credit_used']
        )
        prediction['image_url'] = AnimalPrediction.objects.last().image
    return render(request, 'animal/predict.html', {'prediction': prediction } )


def contact(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        subject = request.POST.get('subject')
        message = request.POST.get('message')

        # Save the contact form data to the database
        Contact.objects.create(
            name=name,
            email=email,
            subject=subject,
            message=message
        )

        return render(request, 'animal/contact.html', {'success': True})
    return render(request, 'animal/contact.html')

def history(request):
    if request.method == 'GET':
        predictions = AnimalPrediction.objects.all().order_by('-created_at')

    
   

    return render(request, 'animal/history.html',{'predictions': predictions}) 