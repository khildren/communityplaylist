from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm
from .models import Event, EventPhoto, Venue


class RegisterForm(forms.Form):
    email    = forms.EmailField(widget=forms.EmailInput(attrs={'placeholder': 'your@email.com'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Password'}))
    confirm  = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Confirm password'}))

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean(self):
        data = super().clean()
        if data.get('password') != data.get('confirm'):
            raise forms.ValidationError('Passwords do not match.')
        return data


class StyledAuthForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'placeholder': 'your@email.com'})
        self.fields['password'].widget.attrs.update({'placeholder': 'Password'})


class EventSubmitForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            'title',
            'category',
            'description',
            'location',
            'neighborhood',
            'start_date',
            'end_date',
            'photo',
            'website',
            'submitted_by',
            'submitted_email',
            'is_free',
            'price_info',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Event name'}),
            'category': forms.Select(attrs={'id': 'id_category'}),
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Tell people about this event'}),
            'location': forms.TextInput(attrs={'placeholder': 'Venue name and address'}),
            'neighborhood': forms.TextInput(attrs={'placeholder': 'e.g. SE, NE, NW, Downtown'}),
            'start_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'end_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'website': forms.URLInput(attrs={'placeholder': 'https://'}),
            'submitted_by': forms.TextInput(attrs={'placeholder': 'Your name'}),
            'submitted_email': forms.EmailInput(attrs={'placeholder': 'Your email (not published)'}),
            'price_info': forms.TextInput(attrs={'placeholder': 'e.g. $10 advance, $15 door'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['start_date'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['end_date'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['end_date'].required = False
        self.fields['neighborhood'].required = False
        self.fields['website'].required = False
        self.fields['submitted_email'].required = False
        self.fields['price_info'].required = False
        self.fields['photo'].required = False
        self.fields['category'].required = False


class VenueForm(forms.ModelForm):
    class Meta:
        model = Venue
        fields = ['name', 'description', 'address', 'neighborhood', 'website', 'logo',
                  'instagram', 'threads', 'bluesky', 'twitter', 'mastodon',
                  'youtube', 'tiktok', 'bandcamp', 'soundcloud', 'mixcloud',
                  'discord', 'medium', 'linkedin']
        widgets = {
            'name':         forms.TextInput(attrs={'placeholder': 'Venue name'}),
            'description':  forms.Textarea(attrs={'rows': 4, 'placeholder': 'About your space — what happens here, what makes it special'}),
            'address':      forms.TextInput(attrs={'placeholder': 'e.g. 123 NW Davis St, Portland, OR 97209'}),
            'neighborhood': forms.TextInput(attrs={'placeholder': 'e.g. Old Town, SE, NE, NW'}),
            'website':      forms.URLInput(attrs={'placeholder': 'https://'}),
            'instagram':    forms.TextInput(attrs={'placeholder': 'yourhandle (no @)'}),
            'threads':      forms.TextInput(attrs={'placeholder': 'yourhandle (no @)'}),
            'bluesky':      forms.TextInput(attrs={'placeholder': 'yourname.bsky.social (no @)'}),
            'twitter':      forms.TextInput(attrs={'placeholder': 'yourhandle (no @)'}),
            'mastodon':     forms.URLInput(attrs={'placeholder': 'https://pdx.social/@yourhandle'}),
            'youtube':      forms.URLInput(attrs={'placeholder': 'https://youtube.com/@yourchannel'}),
            'tiktok':       forms.TextInput(attrs={'placeholder': 'yourhandle (no @)'}),
            'bandcamp':     forms.URLInput(attrs={'placeholder': 'https://yourband.bandcamp.com'}),
            'soundcloud':   forms.TextInput(attrs={'placeholder': 'username or profile slug'}),
            'mixcloud':     forms.TextInput(attrs={'placeholder': 'username or profile slug'}),
            'discord':      forms.URLInput(attrs={'placeholder': 'https://discord.gg/invitecode'}),
            'medium':       forms.TextInput(attrs={'placeholder': 'yourname (no @)'}),
            'linkedin':     forms.TextInput(attrs={'placeholder': 'company-page-slug'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ['description', 'neighborhood', 'website', 'logo',
                  'instagram', 'threads', 'bluesky', 'twitter', 'mastodon',
                  'youtube', 'tiktok', 'bandcamp', 'soundcloud', 'mixcloud',
                  'discord', 'medium', 'linkedin']:
            self.fields[f].required = False


class EventPhotoForm(forms.ModelForm):
    class Meta:
        model = EventPhoto
        fields = ['image', 'caption', 'submitted_by', 'submitted_email']
        widgets = {
            'caption': forms.TextInput(attrs={'placeholder': 'Caption (optional)'}),
            'submitted_by': forms.TextInput(attrs={'placeholder': 'Your name'}),
            'submitted_email': forms.EmailInput(attrs={'placeholder': 'Your email (not published)'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['caption'].required = False
        self.fields['submitted_by'].required = False
        self.fields['submitted_email'].required = False