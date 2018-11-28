from util import get_num_lines, get_pos2idx_idx2pos, index_sequence, get_vocab, embed_indexed_sequence, \
    get_word2idx_idx2word, get_embedding_matrix, write_predictions, get_performance_VUAverb_val, \
    get_performance_VUAverb_test, get_performance_VUA_test
from util import TextDatasetWithGloveElmoSuffix as TextDataset
from util import evaluate
from parser import test_set_parser
from model import RNNSequenceModel

import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable
from torch.utils.data import DataLoader

import csv
import h5py
import ast
import matplotlib.pyplot as plt

print("PyTorch version:")
print(torch.__version__)
print("GPU Detected:")
print(torch.cuda.is_available())
using_GPU = False

"""
1. Data Loading
"""
'''
get raw dataset as a list:
  Each element is a triple:
    a sentence: string
    a list of labels: 
'''
raw_train_rcc = []
with open('./formatted-data/rcc_corpus_train.csv') as f:
    lines = csv.reader(f)
    next(lines)
    for line in lines:
        publication_id = int(line[0])  # publication id number
        word_seq = ast.literal_eval(line[1])        # list of tokens(words)
        label_seq = ast.literal_eval(line[2])       # list of labels('B-(id)' or 'I' or '_')
        assert (len(word_seq) == len(label_seq))
        raw_train_rcc.append([publication_id, word_seq, label_seq])

raw_dev_rcc = []
with open('./formatted-data/rcc_corpus_dev_annotated.csv') as f:
    lines = csv.reader(f)
    next(lines)
    for line in lines:
        publication_id = int(line[0])
        word_seq = ast.literal_eval(line[1])
        label_seq = ast.literal_eval(line[2])
        assert (len(word_seq) == len(label_seq))
        raw_dev_rcc.append([publication_id, word_seq, label_seq])


print('size of training set, validation set: ', len(raw_train_rcc), len(raw_dev_rcc))   # currently 1340158, 33067


"""
2. Data preparation
"""
'''
2. 1
get vocabulary and glove embeddings in raw dataset 
'''
# vocab is a set of words
vocab = get_vocab(raw_train_rcc)
# two dictionaries. <PAD>: 0, <UNK>: 1
word2idx, idx2word = get_word2idx_idx2word(vocab)
# glove_embeddings a nn.Embeddings
glove_embeddings = get_embedding_matrix(word2idx, idx2word, normalization=False)
# elmo_embeddings
elmos_train_rcc = None
elmos_dev_rcc = None
#elmos_train_rcc = h5py.File('../elmo/VUA_train.hdf5', 'r')
#elmos_val_rcc = h5py.File('../elmo/VUA_val.hdf5', 'r')


'''
2. 2
embed the datasets
'''
# raw_train_rcc: publication id, list of words, label sequence, 
# embedded_train_vua: embedded_sentence, labels
# glove로 만든 nn.embedding 통과한게 튀어나옴
embedded_train_rcc = [[embed_indexed_sequence(example[1], word2idx, glove_embeddings, elmos_train_rcc), example[2]]
                      for example in raw_train_rcc]
embedded_dev_rcc = [[embed_indexed_sequence(example[1], word2idx, glove_embeddings, elmos_dev_rcc), example[2]]
                    for example in raw_dev_rcc]


'''
2. 3
set up Dataloader for batching
'''
# Separate the input (embedded_sequence) and labels in the indexed train sets.
# embedded_train_vua: embedded_sentence, labels
train_dataset_rcc = TextDataset([example[0] for example in embedded_train_rcc],
                                [example[1] for example in embedded_train_rcc])
dev_dataset_rcc = TextDataset([example[0] for example in embedded_dev_rcc],
                              [example[1] for example in embedded_dev_rcc])

# Data-related hyperparameters
batch_size = 64
# Set up a DataLoader for the training, development, and test dataset
train_dataloader_rcc = DataLoader(dataset=train_dataset_rcc, batch_size=batch_size, shuffle=True,
                              collate_fn=TextDataset.collate_fn)
dev_dataloader_rcc = DataLoader(dataset=dev_dataset_rcc, batch_size=batch_size,
                            collate_fn=TextDataset.collate_fn)


"""
3. Model training
"""
'''
3. 1 
set up model, loss criterion, optimizer
'''
# Instantiate the model
# embedding_dim = glove + elmo + suffix indicator
# dropout1: dropout on input to RNN
# dropout2: dropout in RNN; would be used if num_layers!=1
# dropout3: dropout on hidden state of RNN to linear layer
#RNNseq_model = RNNSequenceModel(num_classes=2, embedding_dim=300 + 1024, hidden_size=300, num_layers=1, bidir=True,
#                                dropout1=0.5, dropout2=0, dropout3=0.1)
RNNseq_model = RNNSequenceModel(num_classes=2, embedding_dim=300, hidden_size=300, num_layers=1, bidir=True,
                                dropout1=0.5, dropout2=0, dropout3=0.1)
# Move the model to the GPU if available
if using_GPU:
    RNNseq_model = RNNseq_model.cuda()
# Set up criterion for calculating loss
loss_criterion = nn.NLLLoss()
# Set up an optimizer for updating the parameters of the rnn_clf
rnn_optimizer = optim.Adam(RNNseq_model.parameters(), lr=0.005)
# Number of epochs (passes through the dataset) to train the model for.
num_epochs = 10


'''
3. 2
train model
'''
train_loss = []
val_loss = []
performance_matrix = None
val_f1s = []
train_f1s = []
# A counter for the number of gradient updates
num_iter = 0
comparable = []
for epoch in range(num_epochs):
    print("Starting epoch {}".format(epoch + 1))
    for (example_text, example_lengths, labels) in train_dataloader_rcc:
        example_text = Variable(example_text)
        example_lengths = Variable(example_lengths)
        labels = Variable(labels)
        if using_GPU:
            example_text = example_text.cuda()
            example_lengths = example_lengths.cuda()
            labels = labels.cuda()
        # predicted shape: (batch_size, seq_len, 2)
        predicted = RNNseq_model(example_text, example_lengths)
        batch_loss = loss_criterion(predicted.view(-1, 2), labels.view(-1))
        rnn_optimizer.zero_grad()
        batch_loss.backward()
        rnn_optimizer.step()
        num_iter += 1
        # Calculate validation and training set loss and accuracy every 200 gradient updates
        if num_iter % 200 == 0:
            avg_eval_loss = evaluate(dev_dataloader_rcc, RNNseq_model, loss_criterion, using_GPU)
            val_loss.append(avg_eval_loss)
            print("Iteration {}. Validation Loss {}.".format(num_iter, avg_eval_loss))
#             avg_eval_loss, performance_matrix = evaluate(idx2pos, train_dataloader_vua, RNNseq_model,
#                                                          loss_criterion, using_GPU)
#             train_loss.append(avg_eval_loss)
#             train_f1s.append(performance_matrix[:, 2])
#             print("Iteration {}. Training Loss {}.".format(num_iter, avg_eval_loss))

"""
for additional training
"""
rnn_optimizer = optim.Adam(RNNseq_model.parameters(), lr=0.0001)
for epoch in range(10):
    print("Starting epoch {}".format(epoch + 1))
    for (example_text, example_lengths, labels) in train_dataloader_rcc:
        example_text = Variable(example_text)
        example_lengths = Variable(example_lengths)
        labels = Variable(labels)
        if using_GPU:
            example_text = example_text.cuda()
            example_lengths = example_lengths.cuda()
            labels = labels.cuda()
        # predicted shape: (batch_size, seq_len, 2)
        predicted = RNNseq_model(example_text, example_lengths)
        batch_loss = loss_criterion(predicted.view(-1, 2), labels.view(-1))
        rnn_optimizer.zero_grad()
        batch_loss.backward()
        rnn_optimizer.step()
        num_iter += 1
        # Calculate validation and training set loss and accuracy every 200 gradient updates
        if num_iter % 200 == 0:
            avg_eval_loss = evaluate(dev_dataloader_rcc, RNNseq_model, loss_criterion, using_GPU)
            val_loss.append(avg_eval_loss)
            print("Iteration {}. Validation Loss {}.".format(num_iter, avg_eval_loss))

#             avg_eval_loss, performance_matrix = evaluate(idx2pos, train_dataloader_vua, RNNseq_model,
#                                                          loss_criterion, using_GPU)
#             train_loss.append(avg_eval_loss)
#             train_f1s.append(performance_matrix[:, 2])
#             print("Iteration {}. Training Loss {}.".format(num_iter, avg_eval_loss))
#             comparable.append(get_performance())

print("Training done!")



# """
# 3.3
# plot the training process: losses for validation and training dataset
# """
# # plt.figure(0)
# # plt.title('Loss for VUA dataset')
# # plt.xlabel('iteration (unit:200)')
# # plt.ylabel('Loss')
# # plt.plot(val_loss, 'g')
# # plt.plot(train_loss, 'b')
# # plt.legend(['Validation loss', 'Training loss'], loc='upper right')
# # plt.show()

# # plt.figure(1)
# # plt.title('Validation F1 for VUA dataset')
# # plt.xlabel('iteration (unit:200)')
# # plt.ylabel('F1')
# # for i in range(len(idx2pos)):
# #     plt.plot([x[i] for x in val_f1s])
# # plt.legend([idx2pos[i] for i in range(len(idx2pos))], loc='upper left')
# # plt.show()

# # plt.figure(2)
# # plt.title('Training F1 for VUA dataset')
# # plt.xlabel('iteration (unit:200)')
# # plt.ylabel('F1')
# # for i in range(len(idx2pos)):
# #     plt.plot([x[i] for x in train_f1s])
# # plt.legend([idx2pos[i] for i in range(len(idx2pos))], loc='upper left')
# # plt.show()


"""
test on genres by POS tags
"""

print("**********************************************************")
print("Evalutation on test set: ")

raw_test_vua = []
with open('../data/VUAsequence/VUA_seq_formatted_test.csv', encoding='latin-1') as f:
    lines = csv.reader(f)
    next(lines)
    for line in lines:
        # txt_id    sen_ix  sentence    label_seq   pos_seq labeled_sentence
        #pos_seq = ast.literal_eval(line[4])
        label_seq = ast.literal_eval(line[3])
        #assert(len(pos_seq) == len(label_seq))
        assert(len(line[2].split()) == len(label_seq))
        raw_test_vua.append([line[2], label_seq])
print('number of examples(sentences) for test_set ', len(raw_test_vua))


# #elmos_test_vua = h5py.File('../elmo/VUA_test.hdf5', 'r')
# elmos_test_vua = None
# # raw_train_vua: sentence, label_seq, pos_seq
# # embedded_train_vua: embedded_sentence, pos, labels
# embedded_test_vua = [[embed_indexed_sequence(example[0], word2idx, glove_embeddings, elmos_test_vua), example[1]]
#                       for example in raw_test_vua]

# # Separate the input (embedded_sequence) and labels in the indexed train sets.
# # embedded_train_vua: embedded_sentence, pos, labels
# test_dataset_vua = TextDataset([example[0] for example in embedded_test_vua],
#                               [example[1] for example in embedded_test_vua])

# # Set up a DataLoader for the test dataset
# test_dataloader_vua = DataLoader(dataset=test_dataset_vua, batch_size=batch_size,
#                               collate_fn=TextDataset.collate_fn)

# print("Tagging model performance on VUA test set by POS tags: regardless of genres")
# avg_eval_loss = evaluate(test_dataloader_vua, RNNseq_model, loss_criterion, using_GPU)



# """
# write the test prediction on the VUA-verb to a file: sequence prediction
# read and extract to get a comparabel performance on VUA-verb test set.
# """
# def get_comparable_performance_test():
#     result = write_predictions(raw_test_vua, test_dataloader_vua, RNNseq_model, using_GPU, '../data/VUAsequence/VUA_seq_formatted_test.csv')
#     f = open('./predictions/vua_seq_test_predictions_LSTMsequence_vua.csv', 'w')
#     writer = csv.writer(f)
#     writer.writerows(result)
#     f.close()

#     get_performance_VUAverb_test()
#     get_performance_VUA_test()

# get_comparable_performance_test()