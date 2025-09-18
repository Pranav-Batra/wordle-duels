from django.shortcuts import render, redirect
from .forms import UserRegisterForm
from django.contrib import messages
from django.contrib.sites.shortcuts import get_current_site
from django.template.loader import render_to_string
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import EmailMessage
from .tokens import account_activation_token
from django.contrib.auth import get_user_model, login
from django.urls import reverse

def index(request):
    messages_to_display = messages.get_message(request)

    return render(request, "index.html", {"messages": messages_to_display})

def await_verification(request):
    return render(request, "users/await_verification.html")

# Create your views here.

def register(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            messages.success(request, f'Your account has been created! You are now able to log in.')
            return redirect('login')
    else:
        form = UserRegisterForm()
    return render(request, 'users/register.html', {'form': form})

# def register(request):
#     form = UserRegisterForm()
#     if request.method == 'POST':
#         form = UserRegisterForm(request.POST)
#         if form.is_valid():
#             user = form.save()
#             user.is_active = True
#             user.save()
            
#             # current_site = get_current_site(request)
#             # mail_subject = 'Activate WordleDuels Account'
#             # message = render_to_string("users/account_activation_email.html",
#             #                            {"user": user,
#             #                              "domain": current_site.domain,
#             #                              "uid": urlsafe_base64_encode(force_bytes(user.pk)),
#             #                              "token": account_activation_token.make_token(user)
#             #                              })
            
#             # to_email = form.cleaned_data.get('email')
#             # email = EmailMessage(
#             #     mail_subject, message, to=[to_email]
#             # )
#             # email.send()
#             # messages.success(request, 'Please check your email to complete the registration.')
#             # return redirect('await_verification')

#             username = form.cleaned_data.get('username')
#             messages.success(request, 'Account successfully created.')
#             return redirect('chat-home')
#     return render(request, 'users/register.html', {'form': form})

# def activate(request, uidb64, token):
#     User = get_user_model()
#     try:
#         uid = force_str(urlsafe_base64_decode(uidb64))
#         user = User.objects.filter(pk=uid).first()
#     except (TypeError, ValueError, OverflowError, User.DoesNotExist):
#         user = None
    
#     if user is not None and account_activation_token.check_token(user, token):
#         user.is_active = True
#         user.save()

#         login(request, user)

#         messages.success(request, "Your account has been successfully activated")
#         return redirect(reverse('login'))
#     else:
#         messages.error(request, "Your activation link is invalid or expired.")
#         return redirect('index')

 


# Create your views here.
