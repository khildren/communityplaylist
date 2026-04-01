from django import forms
from .models import Topic, Reply


class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ['title', 'body', 'author_name', 'category']
        widgets = {
            'title':       forms.TextInput(attrs={'placeholder': 'Topic title'}),
            'body':        forms.Textarea(attrs={'rows': 6, 'placeholder': 'What would you like to share?'}),
            'author_name': forms.TextInput(attrs={'placeholder': 'Your name'}),
        }


class ReplyForm(forms.ModelForm):
    class Meta:
        model = Reply
        fields = ['body', 'author_name']
        widgets = {
            'body':        forms.Textarea(attrs={'rows': 4, 'placeholder': 'Write a reply…'}),
            'author_name': forms.TextInput(attrs={'placeholder': 'Your name'}),
        }
