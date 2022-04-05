#------------------------------------------------------------------------------+
# This impementation is based on library gensim for word embeddings
#
# @author: Doctorini
# Implementation of Evolutionary Optimizer
# Using EMNA (Estimation of Multivariate Normal Algorithm), CMA-ES, cUMDA
# implementation as EDA (Estimation of distribution algortihms)
#
#------------------------------------------------------------------------------+

#--- IMPORT DEPENDENCIES for EDA and Torch modules----------------------+

import sys
sys.path.append('..\\..\\Text_Cat_Based_EDA')
sys.path.append('..\\..\\Text_Cat_Based_EDA\\utils')
sys.path.append('..\\..\\Text_Cat_Based_EDA\\pretrained_models')
sys.path.append('..\\..\\Text_Cat_Based_EDA\\evolutionary_algorithms')
sys.path.append('..\\..\\Text_Cat_Based_EDA\\evolutionary_algorithms\\EDA')
import torch
import re

from utils.logging_custom import make_logger
from algorithms_models.eda.EDA import EMNA
from algorithms_models.eda.CUMDA import CUMDA
from deap import base
from deap import creator
from deap import tools
from deap import cma
import time
import numpy as np
from numpy import random

import torch.nn as nn
from torch.utils.data import DataLoader
from utils.imbalanced_dataset_sampling import ImbalancedDatasetSampler

# Scikit-learn ----------------------------------------------------------+
from sklearn.metrics import classification_report, accuracy_score, mean_absolute_error, f1_score
from skorch import NeuralNet
import os
#--- CONSTANTS ----------------------------------------------------------------+
import sklearn.metrics as sm

# Defining the optimization problem as argmin(lossFuntion) and the individual
# with continuous codification as update rules for weight based on evolutionary algorithms

creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
creator.create("Individual", list, fitness=creator.FitnessMin)


class EDA_Optimizer(NeuralNet):

    def __init__(self,*args,mode="EDA_EMNA",centroid=0.5,sigma=0.8,individuals=10,generations=5,param_length=0,**kargs):
        super().__init__(*args, **kargs)
        #self.device=torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.mode = mode
        log_exp_run = make_logger(name="experiment_"+self.mode)
        log_exp_run.experiments("Running on device: " + str(self.device))
        self.centroid = centroid
        self.sigma = sigma
        self.generations=generations
        self.param_length=param_length
        self.individuals=individuals

    # Train mode is: "SGD","SGD_MINI_BATCH","EDA_EMNA","EDA_CMA_ES"
    def set_train_mode(self, mode):
        self.mode = mode

    def initialize_criterion(self,*args,**kargs):
        super().initialize_criterion(*args,**kargs)
        return self

    def initialize_module(self,*args,**kargs):
        super().initialize_module(*args, **kargs)
        #self.param_length = sum([p.numel() for p in self.module_.parameters() if p.requires_grad])
        fc_pathern = re.compile("dense\w*") # matching only with full connected layers (fc)
        self.param_length = sum([p.numel() for name, p in self.module_.named_parameters() if p.requires_grad and fc_pathern.match(name)])
        log_exp_run = make_logger(name="experiment_" + self.mode)
        log_exp_run.experiments("Amount of parameters: " + str(self.param_length))
        return self

    def get_module(self):
        return self.module_

    # Sckorch methods: this method fits the estimator using a determinate way defined by
    # mode attr. The main modes for training are: EDA_EMNA, EDA_CMA_ES, EDA_CUMDA for all
    # representation model torch ann
    def fit(self, X, y=None, **fit_params):

        if not self.warm_start or not self.initialized_:
            self.initialize()
        self.X_ = X

        log_exp_run = make_logger(name="experiment_" + self.mode)

        self.load_params(f_params=fit_params["fit_param"]["checkpoint"]) # Continue fitting by metaheuristic from previous model
        log_exp_run.experiments("Loaded fit params from previous model")

        self.mode = fit_params["fit_param"]["mode"]
        path = fit_params["fit_param"]["checkpoint"].split('.')[0] +"_"+self.mode+".pt"
        self.individuals=fit_params["fit_param"]["population_size"]

        self.test_accs = []
        self.train_accs = []
        self.confusion_mtxes = []

        if self.mode == "EDA_EMNA":
            log_exp_run.experiments("Training with EDA_EMNA...")
            start_time = time.time()
            self.train_eda_enma_early_stopping(self.sigma, self.centroid, fit_params["fit_param"]["generations"], X)
            log_exp_run.experiments("Time elapsed for EDA_EMNA: " + str(time.time() - start_time))

        if self.mode == "EDA_CMA_ES":
            log_exp_run.experiments("Training with EDA_CMA_ES...")
            start_time = time.time()
            self.train_eda_cma_es_early_stopping(self.sigma, self.centroid, fit_params["fit_param"]["generations"], X, **fit_params)
            log_exp_run.experiments("Time elapsed for EDA_CMA_ES: " + str(time.time() - start_time))

        if self.mode == "EDA_CUMDA":
            log_exp_run.experiments("Training with EDA_CUMDA...")
            start_time = time.time()
            self.train_eda_cumda_early_stopping(self.sigma, fit_params["fit_param"]["generations"], X, **fit_params)
            log_exp_run.experiments("Time elapsed for EDA_CUMDA: " + str(time.time() - start_time))

        self.save_state(path)

        return self

    def save_state(self, path):
        log_exp_run = make_logger(name="experiment_" + self.mode)

        torch.save({
            'generation': self.generations,
            'model_state_dict': self.module_.state_dict(),
            'mode': self.mode
        }, path)

        log_exp_run.experiments("Checkpoint saved")

    def load_state(self, path, trainable=True):
        if not self.warm_start or not self.initialized_:
            self.initialize()

        path = path+"params_eda.pt"
        if os.path.exists(path):
            checkpoint = torch.load(path)
            log_exp_run = make_logger(name="experiment_" + self.mode)
            if checkpoint is not None:
                self.module_.load_state_dict(checkpoint["model_state_dict"])
                self.mode=checkpoint["mode"]

                if trainable:
                    self.module_.train()
                else:
                    self.module_.eval()

                log_exp_run.experiments("Loaded state from check point with mode: "+self.mode)

    # Sci-kit methods
    def predict(self, X):
        x_train = X['features'].type(torch.LongTensor)
        x_train = x_train.to(self.device)
        self.module_.to(self.device)
        prob = self.module_(x_train)
        _, predicted = torch.max(prob.data, 1)
        return predicted.cpu().numpy()[0]

    # Skorch methods: Compute the loss function using hierarchical softmax on gensim module
    def get_loss(self, X, y=None):
        train_loss = 0
        criterion = nn.CrossEntropyLoss()
        iter_data = DataLoader(X, batch_size=self.module__batch_size, sampler=ImbalancedDatasetSampler(X))
        log_exp_run = make_logger(name="experiment_" + self.mode)
        self.module_.to(self.device)

        with torch.no_grad():
            for bach in iter_data:
                x_train = bach['features'].type(torch.LongTensor)
                y_train = bach['labels'].type(torch.LongTensor)
                x_train = x_train.to(self.device)
                y_train = y_train.to(self.device)
                prob = self.self.module_cnn_.forward(x_train)
                loss = criterion(prob, y_train)
                train_loss += loss.item()

        log_exp_run.experiments("Cross-entropy loss for each fold: " + str(train_loss))
        return train_loss

    # Scorch methods: Compute the loss function using softmax and compute score by accuracy
    def score(self, X, y=None):
        train_loss = 0
        criterion = nn.CrossEntropyLoss()
        iter_data = DataLoader(X, batch_size=self.module__batch_size, shuffle=True)
        log_exp_run = make_logger(name="experiment_" + self.mode)

        predictions = []
        labels = []
        self.module_.to(self.device)
        self.module_.eval()

        with torch.no_grad():
            for bach in iter_data:
                x_test = bach['features'].type(torch.LongTensor)
                y_test = bach['labels'].type(torch.LongTensor)
                x_test = x_test.to(self.device)
                y_test = y_test.to(self.device)
                prob = self.module_(x_test)
                loss = criterion(prob, y_test)
                train_loss += loss.item()
                _, predicted = torch.max(prob.data, 1)
                predictions.extend(predicted.cpu().numpy())
                labels.extend(y_test.cpu().numpy())

        accuracy = accuracy_score(labels, predictions)
        mae = mean_absolute_error(labels, predictions)
        macro_f1 = f1_score(labels, predictions, average='macro')

        log_exp_run.experiments("Cross-entropy loss for each fold: " + str(train_loss))
        log_exp_run.experiments("Accuracy for each fold: " + str(accuracy))
        log_exp_run.experiments("\n" + classification_report(labels, predictions))
        log_exp_run.experiments("\nMean Absolute Error (MAE): " + str(mae))
        log_exp_run.experiments("\nMacro F1: " + str(macro_f1))
        return accuracy

    def score_unbalanced(self, X, y=None, is_unbalanced=True, print_logs=False):
        train_loss = 0
        criterion = nn.CrossEntropyLoss()
        iter_data = DataLoader(X, batch_size=self.module__batch_size, sampler=ImbalancedDatasetSampler(X)) if is_unbalanced else DataLoader(X, batch_size=self.module__batch_size, shuffle=True)
        log_exp_run = make_logger(name="experiment_" + self.mode)

        predictions = []
        labels = []
        self.module_.to(self.device)
        self.module_.eval()

        with torch.no_grad():
            for bach in iter_data:
                x_test = bach['features'].type(torch.LongTensor)
                y_test = bach['labels'].type(torch.LongTensor)
                x_test = x_test.to(self.device)
                y_test = y_test.to(self.device)
                prob = self.module_(x_test)
                loss = criterion(prob, y_test)
                train_loss += loss.item()
                _, predicted = torch.max(prob.data, 1)
                predictions.extend(predicted.cpu().numpy())
                labels.extend(y_test.cpu().numpy())

        accuracy = accuracy_score(labels, predictions)

        if print_logs:
            mae = mean_absolute_error(labels, predictions)
            macro_f1 = f1_score(labels, predictions, average='macro')

            log_exp_run.experiments("Cross-entropy loss for each fold: " + str(train_loss))
            log_exp_run.experiments("Accuracy for each fold: " + str(accuracy))
            log_exp_run.experiments("\n"+classification_report(labels, predictions))
            log_exp_run.experiments("\nMean Absolute Error (MAE)" + str(mae))
            log_exp_run.experiments("\nMacro F1 (MAE)" + str(macro_f1))

        confusion_mtx = sm.confusion_matrix(labels, predictions)
        return accuracy, confusion_mtx

    # Training of tensor model using EMNA as EDA algorithms, with early stopping
    def train_eda_enma_early_stopping(self, sigma, centroid, generations, data):
        log_exp_run = make_logger(name="experiment_" + self.mode)
        iter_data = DataLoader(data, batch_size=self.module__batch_size, sampler=ImbalancedDatasetSampler(data))

        # LAMBDA is the size of the population
        # N is the size of individual, the number of parameters on ANN
        N, LAMBDA = self.param_length, self.individuals

        # MU intermediate set of LAMBDA
        MU = int(LAMBDA / 4)

        # Creating an instance of EMNA
        strategy = EMNA(centroid=[centroid] * N, sigma=sigma, mu=MU, lambda_=LAMBDA)

        toolbox = base.Toolbox()
        toolbox.register("evaluate", loss_function, model=self.module_, training_data=iter_data, device=self.device)
        toolbox.register("generate", strategy.generate, creator.Individual)
        toolbox.register("update", strategy.update)
        random.seed(int(time.time()))

        hof = tools.HallOfFame(1, similar=np.array_equal)

        # Define statisticals metrics
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("std", np.std)
        stats.register("min", np.min)
        stats.register("max", np.max)

        logbook = tools.Logbook()
        logbook.header = ['gen', 'nevals'] + (stats.fields if stats else [])

        # Param for early stoping
        t = 0
        STAGNATION_ITER = 100  # int(np.ceil(0.2 * t + 120 + 30. * N / LAMBDA))
        min_std = 1e-3
        conditions = {"MaxIter": False, "Stagnation": False}

        for gen in range(generations):
            # Generate a new population
            population = toolbox.generate()
            # Evaluate the individuals
            fitnesses = toolbox.map(toolbox.evaluate, population)
            for ind, fit in zip(population, fitnesses):
                ind.fitness.values = fit

            if hof is not None:
                hof.update(population)

            # Update the strategy with the evaluated individuals
            toolbox.update(population)

            record = stats.compile(population) if stats is not None else {}
            logbook.record(gen=gen, nevals=len(population), **record)

            t += 1

            if t >= generations:
                # The maximum number of iteration
                conditions["MaxIter"] = True

            means_values = logbook.select("avg")
            if len(means_values) > STAGNATION_ITER and np.median(means_values[-20:]) >= np.median(
                    means_values[-STAGNATION_ITER:-STAGNATION_ITER + 20]) or record["std"] <= min_std:
                # The stagnation condition
                conditions["Stagnation"] = True

            if any(conditions.values()):
                break

        stop_causes = [k for k, v in conditions.items() if v]
        log_exp_run.experiments("Stopped because of condition")
        for cause in stop_causes:
            log_exp_run.experiments(cause)

        best_solution = hof[0]
        series_fitness = [i.fitness.values[0] for i in population]
        fix_individual_to_fc_layers(best_solution, self.module_, self.device)

        log_exp_run.experiments("Results for EDA_EMNA\r\n")
        log_exp_run.experiments("Parameters: \r\n")
        log_exp_run.experiments("Sigma: " + str(sigma) + " centroid: " + str(centroid) + "\r\n")
        log_exp_run.experiments("\r\n" + str(logbook) + "\r\n")
        log_exp_run.experiments(list(series_fitness))

    # Training tensor model using CUMDA as EDA algorithms, with early stopping
    def train_eda_cumda_early_stopping(self, sigma, generations, data,**fit_params):
        log_exp_run = make_logger(name="experiment_" + self.mode)
        iter_data = DataLoader(data, batch_size=self.module__batch_size, sampler=ImbalancedDatasetSampler(data))
        # LAMBDA is the size of the population
        # N is the size of individual, the number of parameters on ANN

        N, LAMBDA = self.param_length, self.individuals

        MU = int(LAMBDA / 4)

        toolbox = base.Toolbox()

        toolbox.register("evaluate", loss_function, model=self.module_, training_data=iter_data, device=self.device)
        random.seed(int(time.time()))

        # creating an instance of CUMDA
        strategy = CUMDA(N, sigma=sigma, mu=MU, lambda_=LAMBDA)
        toolbox.register("generate", strategy.generate, creator.Individual)
        toolbox.register("update", strategy.update)
        hof = tools.HallOfFame(1)

        # Define statisticals metrics
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("std", np.std)
        stats.register("min", np.min)
        stats.register("max", np.max)

        logbook = tools.Logbook()
        logbook.header = ['gen', 'nevals'] + (stats.fields if stats else [])

        # Param for early stopping
        t = 0
        STAGNATION_ITER = 100  # int(np.ceil(0.2 * t + 120 + 30. * N / LAMBDA))
        min_std = 1e-4
        conditions = {"MaxIter": False, "Stagnation": False}

        # population_result=[]
        for gen in range(generations):
            # Generate a new population
            population = toolbox.generate()
            # Evaluate the individuals
            fitnesses = toolbox.map(toolbox.evaluate, population)
            for ind, fit in zip(population, fitnesses):
                ind.fitness.values = fit

            if hof is not None:
                hof.update(population)

            # Update the strategy with the evaluated individuals
            toolbox.update(population)

            record = stats.compile(population) if stats is not None else {}
            logbook.record(gen=gen, nevals=len(population), **record)

            # Test acc and confusion matrix  charts
            best_solution = hof[0]
            fix_individual_to_fc_layers(best_solution, self.module_, self.device)
            test_acc, confusion_mtx = self.score_unbalanced(X=fit_params["test_data"] if fit_params.get('fit_param')
                                            is None else fit_params["fit_param"]["test_data"], is_unbalanced=False)
            self.test_accs.append(test_acc)
            self.confusion_mtxes.append(confusion_mtx)
            self.train_accs.append(self.score_unbalanced(data,is_unbalanced=True))

            # print(self.logbook.stream)
            t += 1

            if t >= generations:
                # The maximum number of iteration
                conditions["MaxIter"] = True

            means_values = logbook.select("avg")
            if len(means_values) > STAGNATION_ITER and np.median(means_values[-20:]) >= np.median(
                    means_values[-STAGNATION_ITER:-STAGNATION_ITER + 20]) or record["std"] <= min_std:
                # The stagnation condition
                conditions["Stagnation"] = True

            if any(conditions.values()):
                break

        stop_causes = [k for k, v in conditions.items() if v]
        log_exp_run.experiments("Stopped because of condition")
        for cause in stop_causes:
            log_exp_run.experiments(cause)

        best_solution = hof[0]
        series_fitness = [i.fitness.values[0] for i in population]
        fix_individual_to_fc_layers(best_solution, self.module_, self.device)

        log_exp_run.experiments("Results for EDA_CUMDA\r\n")
        log_exp_run.experiments("Parameters: \r\n")
        log_exp_run.experiments("Sigma: " + str(sigma) + "\r\n")
        log_exp_run.experiments("\r\n" + str(logbook) + "\r\n")
        log_exp_run.experiments(list(series_fitness))

    # Training tensor model using CMA-ES as EDA algorithms, with early stopping
    def train_eda_cma_es_early_stopping(self, sigma, centroid, generations, data,**fit_params):
        log_exp_run = make_logger(name="experiment_" + self.mode)
        iter_data = DataLoader(data, batch_size=self.module__batch_size, sampler=ImbalancedDatasetSampler(data))
        # LAMBDA is the size of the population
        # N is the size of individual, the number of parameters on ANN
        N, LAMBDA = self.param_length, self.individuals

        toolbox = base.Toolbox()
        toolbox.register("evaluate", loss_function, model=self.module_, training_data=iter_data, device=self.device)
        np.random.seed(int(time.time()))

        # creating an instance of CMA
        strategy = cma.Strategy(centroid=[centroid] * N, sigma=sigma, lambda_=LAMBDA)#cma.Strategy
        toolbox.register("generate", strategy.generate, creator.Individual)
        toolbox.register("update", strategy.update)
        hof = tools.HallOfFame(1)

        # Define statisticals metrics
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("std", np.std)
        stats.register("min", np.min)
        stats.register("max", np.max)

        logbook = tools.Logbook()
        logbook.header = ['gen', 'nevals'] + (stats.fields if stats else [])

        # Param for early stopping
        t = 0
        STAGNATION_ITER = 100  # int(np.ceil(0.2 * t + 120 + 30. * N / LAMBDA))
        min_std = 1e-4
        conditions = {"MaxIter": False, "Stagnation": False}

        for gen in range(generations):
            # Generate a new population
            population = toolbox.generate()
            # Evaluate the individuals
            fitnesses = toolbox.map(toolbox.evaluate, population)
            for ind, fit in zip(population, fitnesses):
                ind.fitness.values = fit

            if hof is not None:
                hof.update(population)

            # Update the strategy with the evaluated individuals
            toolbox.update(population)

            record = stats.compile(population) if stats is not None else {}
            logbook.record(gen=gen, nevals=len(population), **record)

            # Test acc and confusion matrix  charts
            best_solution = hof[0]
            fix_individual_to_fc_layers(best_solution, self.module_, self.device)
            test_acc, confusion_mtx = self.score_unbalanced(X=fit_params["test_data"] if fit_params.get('fit_param')
                                            is None else fit_params["fit_param"]["test_data"], is_unbalanced=False)
            self.test_accs.append(test_acc)
            self.confusion_mtxes.append(confusion_mtx)
            self.train_accs.append(self.score(data))

            # print(self.logbook.stream)
            t += 1

            if t >= generations:
                # The maximum number of iteration
                conditions["MaxIter"] = True

            means_values = logbook.select("avg")
            if len(means_values) > STAGNATION_ITER and np.median(means_values[-20:]) >= np.median(
                    means_values[-STAGNATION_ITER:-STAGNATION_ITER + 20]) or record["std"] <= min_std:
                # The stagnation condition
                conditions["Stagnation"] = True

            if any(conditions.values()):
                break

        stop_causes = [k for k, v in conditions.items() if v]
        log_exp_run.experiments("Stopped because of condition")
        for cause in stop_causes:
            log_exp_run.experiments(cause)

        best_solution = hof[0]
        series_fitness = [i.fitness.values[0] for i in population]
        fix_individual_to_fc_layers(best_solution, self.module_, self.device)

        log_exp_run.experiments("Results for EDA_CMA-ES\r\n")
        log_exp_run.experiments("Parameters: \r\n")
        log_exp_run.experiments("Sigma: " + str(sigma) + " centroid: " + str(centroid) + "\r\n")
        log_exp_run.experiments("\r\n" + str(logbook) + "\r\n")
        log_exp_run.experiments(list(series_fitness))

    # Test model with test examples for evaluate accuracy an return confusion matrix
    def test_model(self,dataset_test):
        predictions=[]
        labels=[]
        self.module_.to(self.device)
        self.module_.eval()
        iter_data = DataLoader(dataset_test, batch_size=self.module__batch_size, shuffle=True)
        log_exp_run = make_logger(name="experiment_" + self.mode)
        with torch.no_grad():
            for batch in iter_data:
                x_test = batch['features'].type(torch.LongTensor)
                y_test = batch['labels'].type(torch.LongTensor)
                x_test = x_test.to(self.device)
                y_test = y_test.to(self.device)
                prob = self.module_(x_test)
                _,predicted=torch.max(prob.data,1)
                predictions.extends(predicted.cpu().numpy())
                labels.extends(y_test.cpu().numpy())

        log_exp_run.experiments(classification_report(labels,predictions))


#  Compute loss with examples giving a single individual and Tensor model
def loss_function(individual, model, training_data, device):
    #fix_individual_to_layers(individual, model, device)
    fix_individual_to_fc_layers(individual, model, device)
    train_loss = 0
    criterion = nn.CrossEntropyLoss()
    model.to(device)

    with torch.no_grad():
        for bach in training_data:
            x_train = bach['features'].type(torch.LongTensor)
            y_train = bach['labels'].type(torch.LongTensor)
            x_train = x_train.to(device)
            y_train = y_train.to(device)
            prob = model.forward(x_train)
            loss = criterion(prob, y_train)
            train_loss += loss.item()

    return train_loss,


#  Fix individual to each layer in params model
def fix_individual_to_layers(individual, model, device):
    index = 0
    individual_tensor = torch.tensor(individual)
    individual_tensor.to(device)
    model.to(device)
    for p in model.parameters():
        if p.requires_grad:
            len_p = p.numel()
            aux_tensor = individual_tensor[index:index + len_p]
            p.data = aux_tensor.reshape(p.shape).data
            index += len_p


#  Fix individual to full-connected layer in params model
def fix_individual_to_fc_layers(individual, model, device):
    index = 0
    individual_tensor = torch.tensor(individual)
    individual_tensor.to(device)
    model.to(device)
    fc_pathern = re.compile("dense\w*") # matching only with full connected layers (fc)
    for name, p in model.named_parameters():
        if p.requires_grad and fc_pathern.match(name):
            len_p = p.numel()
            aux_tensor = individual_tensor[index:index + len_p]
            p.data = aux_tensor.reshape(p.shape).data
            index += len_p
