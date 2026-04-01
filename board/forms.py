from django import forms
from .models import Topic, Reply


class TopicForm(forms.ModelForm):
    # Honeypot — hidden field, must stay empty
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'style': 'display:none!important',
        'tabindex': '-1',
        'autocomplete': 'off',
    }))

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
