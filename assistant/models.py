from django.db import models

# Create your models here.

class ConversationAssistant(models.Model):
    client = models.ForeignKey('users.Client', on_delete=models.CASCADE, related_name='conversations_assistant')
    debute_le = models.DateTimeField(auto_now_add=True)
    termine_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'conversations_assistant'
        indexes = [models.Index(fields=['client'])]

class MessageAssistant(models.Model):
    class Emeteur(models.TextChoices):
        CLIENT = 'client', 'Client'
        ASSISTANT = 'assistant', 'Assistant'

    conversation = models.ForeignKey(ConversationAssistant, on_delete=models.CASCADE, related_name='messages')
    emetteur = models.CharField(max_length=10, choices=Emetteur.choices)
    contenu = models.TextField()
    fonction_appelee = models.CharField(max_length=100, null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'messages_assistant'
        indexes = [models.Index(fields=['conversation', 'cree_le'])]