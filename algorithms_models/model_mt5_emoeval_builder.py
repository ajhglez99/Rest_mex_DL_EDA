from builtins import super
from collections import OrderedDict

import torch.nn as nn
from transformers.models.mt5 import MT5ForConditionalGeneration
from transformers.modeling_outputs import TokenClassifierOutput
import torch

class CustomMT5Model(nn.Module):
    def __init__(self, labels):
        super(CustomMT5Model, self).__init__()
        self.labels = labels

        # Load Model with given checkpoint and extract its body
        self.model = MT5ForConditionalGeneration.from_pretrained("google/mt5-small")
        self.config =self.model.config
        self.dropout = nn.Dropout(0.1)
        self.fc = nn.Linear(512, labels)  # load and initialize weights

    def forward(self, input_ids=None, attention_mask=None, labels=None, labels_ids=None):
        # Extract outputs from the body
        outputs = self.model(input_ids=input_ids, labels=labels, attention_mask=attention_mask)

        # Add custom layers
        sequence_output = self.dropout(outputs.encoder_last_hidden_state )  # outputs[0]=last hidden state
        sequence_output = sequence_output[:, 0, :]
        sequence_output = torch.reshape(sequence_output,(-1, 512))
        logits = self.fc(sequence_output)  # calculate losses torch.reshape(sequence_output,(-1, 4096))

        loss = None
        if labels is not None:
            loss_fct = nn.CrossEntropyLoss()
            logits=logits.view(-1, self.labels)
            loss = loss_fct(logits, labels_ids.view(-1))

        return TokenClassifierOutput(loss=loss, logits=logits, hidden_states=outputs.encoder_last_hidden_state,
                                     attentions=outputs.encoder_attentions)