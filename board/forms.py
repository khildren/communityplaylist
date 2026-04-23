from django import forms
from .models import Topic, Reply, Offering


class TopicForm(forms.ModelForm):
    # Honeypot — visible field bots fill in, humans don't see
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'style': 'display:none!important',
        'tabindex': '-1',
        'autocomplete': 'off',
    }))
    # Timing honeypot — JS stamps epoch on page load; bots submit too fast
    _t = forms.CharField(required=False, widget=forms.HiddenInput(attrs={'id': 'id__t_topic'}))

    class Meta:
        model = Topic
        fields = ['title', 'body', 'author_name', 'category']
        widgets = {
            'title':       forms.TextInput(attrs={'placeholder': 'Topic title'}),
            'body':        forms.Textarea(attrs={'rows': 6, 'placeholder': 'What would you like to share?'}),
            'author_name': forms.TextInput(attrs={'placeholder': 'Your name'}),
        }

    def clean_website(self):
        val = self.cleaned_data.get('website', '')
        if val:
            raise forms.ValidationError('Bot detected.')
        return val


class ReplyForm(forms.ModelForm):
    # Honeypot
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'style': 'display:none!important',
        'tabindex': '-1',
        'autocomplete': 'off',
    }))
    # Timing honeypot
    _t = forms.CharField(required=False, widget=forms.HiddenInput(attrs={'id': 'id__t_reply'}))

    class Meta:
        model = Reply
        fields = ['body', 'author_name']
        widgets = {
            'body':        forms.Textarea(attrs={'rows': 4, 'placeholder': 'Write a reply…'}),
            'author_name': forms.TextInput(attrs={'placeholder': 'Your name'}),
        }

    def clean_website(self):
        val = self.cleaned_data.get('website', '')
        if val:
            raise forms.ValidationError('Bot detected.')
        return val


class OfferingForm(forms.ModelForm):
    # Honeypot
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'style': 'display:none!important',
        'tabindex': '-1',
        'autocomplete': 'off',
    }))
    # Timing honeypot
    _t = forms.CharField(required=False, widget=forms.HiddenInput(attrs={'id': 'id__t_offer'}))

    # "Not listed" escape hatch — handled in the view
    new_neighborhood_name = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Cully, Lents, St. Johns, Tigard…',
            'id': 'id_new_hood',
        }),
    )

    class Meta:
        model = Offering
        fields = ['title', 'body', 'category', 'photo', 'contact_hint', 'neighborhood', 'author_name']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'e.g. "Free couch — SE Portland" or "ISO: road bike"',
            }),
            'body': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Describe the item — condition, size, pickup details, any requirements…',
            }),
            'contact_hint': forms.TextInput(attrs={
                'placeholder': 'e.g. reply to this thread · @handle on Telegram · Discord ID · Signal username',
            }),
            'author_name': forms.TextInput(attrs={'placeholder': 'Your name or handle'}),
            'neighborhood': forms.Select(attrs={'id': 'id_neighborhood_select'}),
        }

    def clean_website(self):
        val = self.cleaned_data.get('website', '')
        if val:
            raise forms.ValidationError('Bot detected.')
        return val
