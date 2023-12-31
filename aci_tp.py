# -*- coding: utf-8 -*-
"""ACI_TP.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1Sox9yCsRzfkiyYV5qP9hxDm5PXtYpGxn
"""

#pandas                        1.5.3
#scikit-learn                  1.2.2
#numpy                         1.22.4
#!pip list

import pandas as pd
import numpy as np

from abc import ABCMeta
from sklearn.base import ClassifierMixin
from sklearn.ensemble._base import BaseEnsemble, _partition_estimators

from abc import ABCMeta, abstractmethod
from sklearn.utils.parallel import delayed, Parallel
from sklearn.tree._tree import DTYPE, DOUBLE
from sklearn.tree import DecisionTreeClassifier
from sklearn.utils import check_random_state, compute_sample_weight
from sklearn.utils.multiclass import type_of_target
from sklearn.metrics import accuracy_score
from sklearn.utils.validation import (
    check_is_fitted,
    _check_sample_weight,
)
from sklearn import preprocessing
from sklearn.ensemble._forest import BaseForest, ForestClassifier
from sklearn.model_selection import cross_validate
import threading

class RandomForestClassifier(ForestClassifier):

    def __init__(
        self,
        n_estimators=100,
        *,
        criterion="gini",
        max_depth=None,
        min_samples_split=2,
        min_samples_leaf=1,
        min_weight_fraction_leaf=0.0,
        max_features="sqrt",
        max_leaf_nodes=None,
        min_impurity_decrease=0.0,
        bootstrap=True,
        oob_score=False,
        n_jobs=None,
        random_state=None,
        verbose=0,
        warm_start=False,
        class_weight=None,
        ccp_alpha=0.0,
        max_samples=None,
        sampling_strategy=None,
        replacement=False,
    ):
        super().__init__(
            estimator=DecisionTreeClassifier(),
            n_estimators=n_estimators,
            estimator_params=(
                "criterion",
                "max_depth",
                "min_samples_split",
                "min_samples_leaf",
                "min_weight_fraction_leaf",
                "max_features",
                "max_leaf_nodes",
                "min_impurity_decrease",
                "random_state",
                "ccp_alpha",
            ),
            bootstrap=bootstrap,
            oob_score=oob_score,
            n_jobs=n_jobs,
            random_state=random_state,
            verbose=verbose,
            warm_start=warm_start,
            class_weight=class_weight,
            max_samples=max_samples,
        )

        self.criterion = criterion
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.min_samples_leaf = min_samples_leaf
        self.min_weight_fraction_leaf = min_weight_fraction_leaf
        self.max_features = max_features
        self.max_leaf_nodes = max_leaf_nodes
        self.min_impurity_decrease = min_impurity_decrease
        self.ccp_alpha = ccp_alpha

        self.sampling_strategy = sampling_strategy
        self.replacement = replacement
        self._estimator = self.estimator if hasattr(self, "estimator") else self.base_estimator
        
        self._sampling_strategy = self.sampling_strategy

        self.base_sampler_ = RandomUnderSampler(
            sampling_strategy=self._sampling_strategy,
            replacement=self.replacement,
        )

    def _make_sampler_estimator(self):

        estimator = clone(self._estimator)
        estimator.set_params(**{p: getattr(self, p) for p in self.estimator_params})
        sampler = clone(self.base_sampler_)

        return estimator, sampler

    def fit(self, X, y):

        if not self.sampling_strategy:
          return super().fit(X,y)

        X, y = self._validate_data(
            X, y, multi_output=True, accept_sparse="csc", dtype=DTYPE
        )

        self._n_features = X.shape[1]

        y = np.atleast_1d(y)

        if y.ndim == 1:
            y = np.reshape(y, (-1, 1))

        self.n_outputs_ = y.shape[1]

        y_encoded, _ = self._validate_y_class_weight(y)

        if getattr(y, "dtype", None) != DOUBLE or not y.flags.contiguous:
            y_encoded = np.ascontiguousarray(y_encoded, dtype=DOUBLE)

        # Get bootstrap sample size
        n_samples_bootstrap = _get_n_samples_bootstrap(
            n_samples=X.shape[0], max_samples=self.max_samples
        )

        self.estimators_ = []
        self.samplers_ = []
        self.pipelines_ = []

        n_more_estimators = self.n_estimators - len(self.estimators_)

        trees = []
        samplers = []
        for _ in range(n_more_estimators):
            tree, sampler = self._make_sampler_estimator()
            trees.append(tree)
            samplers.append(sampler)

        samplers_trees = Parallel(
            n_jobs=self.n_jobs,
            verbose=self.verbose,
            prefer="threads",
        )(
            delayed(_local_parallel_build_trees)(
                s,
                t,
                self.bootstrap,
                X,
                y_encoded,
                i,
                len(trees),
                verbose=self.verbose,
                class_weight=self.class_weight,
                n_samples_bootstrap=n_samples_bootstrap,
                forest=self,
            )
            for i, (s, t) in enumerate(zip(samplers, trees))
        )
        samplers, trees = zip(*samplers_trees)

        # Collect newly grown trees
        self.estimators_.extend(trees)
        self.samplers_.extend(samplers)

        # Create pipeline with the fitted samplers and trees
        self.pipelines_.extend(
            [
                make_pipeline(deepcopy(s), deepcopy(t))
                for s, t in zip(samplers, trees)
            ]
        )

        if self.oob_score:
            self._set_oob_score_and_attributes(X, y_encoded)

        # Decapsulate classes_ attributes
        if hasattr(self, "classes_") and self.n_outputs_ == 1:
            self.n_classes_ = self.n_classes_[0]
            self.classes_ = self.classes_[0]

        return self

    def _set_oob_score_and_attributes(self, X, y):

        self.oob_decision_function_ = self._compute_oob_predictions(X, y)
        if self.oob_decision_function_.shape[-1] == 1:
            # drop the n_outputs axis if there is a single output
            self.oob_decision_function_ = self.oob_decision_function_.squeeze(axis=-1)
        from sklearn.metrics import accuracy_score

        self.oob_score_ = accuracy_score(
            y, np.argmax(self.oob_decision_function_, axis=1)
        )

    def _compute_oob_predictions(self, X, y):

        n_samples = y.shape[0]
        n_outputs = self.n_outputs_

        oob_pred_shape = (n_samples, self.n_classes_[0], n_outputs)

        oob_pred = np.zeros(shape=oob_pred_shape, dtype=np.float64)
        n_oob_pred = np.zeros((n_samples, n_outputs), dtype=np.int64)

        for sampler, estimator in zip(self.samplers_, self.estimators_):
            X_resample = X[sampler.sample_indices_]
            y_resample = y[sampler.sample_indices_]

            n_sample_subset = y_resample.shape[0]
            n_samples_bootstrap = _get_n_samples_bootstrap(
                n_sample_subset, self.max_samples
            )

            unsampled_indices = _generate_unsampled_indices(
                estimator.random_state, n_sample_subset, n_samples_bootstrap
            )

            y_pred = self._get_oob_predictions(
                estimator, X_resample[unsampled_indices, :]
            )

            indices = sampler.sample_indices_[unsampled_indices]
            oob_pred[indices, ...] += y_pred
            n_oob_pred[indices, :] += 1

        for k in range(n_outputs):
            oob_pred[..., k] /= n_oob_pred[..., [k]]

        return oob_pred

from sklearn.base import clone

import numpy as np
from sklearn.utils import _safe_indexing, check_random_state
from sklearn.utils import column_or_1d

import numbers
from collections.abc import Mapping

from sklearn.base import OneToOneFeatureMixin
from sklearn.base import BaseEstimator
from collections import OrderedDict
from sklearn.pipeline import make_pipeline
from copy import deepcopy
from sklearn.utils import column_or_1d
from sklearn.ensemble._forest import (
    _generate_unsampled_indices,
    _get_n_samples_bootstrap,
    _parallel_build_trees,
)

def _local_parallel_build_trees(
    sampler,
    tree,
    bootstrap,
    X,
    y,
    tree_idx,
    n_trees,
    verbose=0,
    class_weight=None,
    n_samples_bootstrap=None,
    forest=None,
):
    # resample before to fit the tree
    X_resampled, y_resampled = sampler.fit_resample(X, y)
    if _get_n_samples_bootstrap is not None:
        n_samples_bootstrap = min(n_samples_bootstrap, X_resampled.shape[0])

    tree = _parallel_build_trees(
        tree,
        forest,
        X_resampled,
        y_resampled,
        None,
        tree_idx,
        n_trees,
        verbose=verbose,
        class_weight=class_weight,
        n_samples_bootstrap=n_samples_bootstrap,
    )
    return sampler, tree


def _count_class_sample(y):
    unique, counts = np.unique(y, return_counts=True)
    return dict(zip(unique, counts))


def _sampling_strategy(sampling_strategy, y, sampling_type):

    target_stats = _count_class_sample(y)
    n_sample_minority = min(target_stats.values())
    class_minority = min(target_stats, key=target_stats.get)
    sampling_strategy_ = {
        key: int(n_sample_minority / sampling_strategy)
        for (key, value) in target_stats.items()
        if key != class_minority
    }

    return sampling_strategy_


def check_sampling_strategy(sampling_strategy, y, sampling_type, **kwargs):

    return OrderedDict(
        sorted(
            _sampling_strategy(sampling_strategy, y, sampling_type).items()
        )
    )
        
class ArraysTransformer:
    def __init__(self, X, y):
        self.x_props = self._gets_props(X)
        self.y_props = self._gets_props(y)

    def transform(self, X, y):
        X = self._transfrom_one(X, self.x_props)
        y = self._transfrom_one(y, self.y_props)
        return X, y

    def _gets_props(self, array):
        props = {}
        props["type"] = array.__class__.__name__
        props["columns"] = getattr(array, "columns", None)
        props["name"] = getattr(array, "name", None)
        props["dtypes"] = getattr(array, "dtypes", None)
        return props

    def _transfrom_one(self, array, props):
        type_ = props["type"].lower()
        if type_ == "list":
            ret = array.tolist()
        elif type_ == "dataframe":

            ret = pd.DataFrame(array, columns=props["columns"])
            ret = ret.astype(props["dtypes"])
        elif type_ == "series":

            ret = pd.Series(array, dtype=props["dtypes"], name=props["name"])
        else:
            ret = array
        return ret

class SamplerMixin(BaseEstimator, metaclass=ABCMeta):

    def fit_resample(self, X, y):

        arrays_transformer = ArraysTransformer(X, y)
        X, y = self._check_X_y(X, y)

        self.sampling_strategy_ = check_sampling_strategy(
            self.sampling_strategy, y, self._sampling_type
        )

        output = self._fit_resample(X, y)

        y_ = output[1]

        X_, y_ = arrays_transformer.transform(output[0], y_)
        return (X_, y_) if len(output) == 2 else (X_, y_, output[2])


class RandomUnderSampler(SamplerMixin, OneToOneFeatureMixin):
    _sampling_type = "under-sampling"

    def __init__(
        self, *, sampling_strategy="auto", random_state=None, replacement=False
    ):
        self.sampling_strategy = sampling_strategy
        self.random_state = random_state
        self.replacement = replacement

    def _check_X_y(self, X, y):
        y = column_or_1d(y)
        X, y = self._validate_data(
            X,
            y,
            reset=True,
            accept_sparse=["csr", "csc"],
            dtype=None,
            force_all_finite=False,
        )
        return X, y

    def fit(self, X, y):

        X, y = self._check_X_y(X, y)
        self.sampling_strategy_ = check_sampling_strategy(
            self.sampling_strategy, y, self._sampling_type
        )
        return self

    def _fit_resample(self, X, y):
        random_state = check_random_state(self.random_state)

        idx_under = np.empty((0,), dtype=int)

        for target_class in np.unique(y):
            if target_class in self.sampling_strategy_.keys():
                n_samples = self.sampling_strategy_[target_class]
                index_target_class = random_state.choice(
                    range(np.count_nonzero(y == target_class)),
                    size=n_samples,
                    replace=self.replacement,
                )
            else:
                index_target_class = slice(None)

            idx_under = np.concatenate(
                (
                    idx_under,
                    np.flatnonzero(y == target_class)[index_target_class],
                ),
                axis=0,
            )

        self.sample_indices_ = idx_under

        return _safe_indexing(X, idx_under), _safe_indexing(y, idx_under)

#from google.colab import drive
#drive.mount('/content/drive')

le = preprocessing.LabelEncoder()

#dfBNG = pd.read_csv("/content/drive/MyDrive/School/2223/ACI/TP/Datasets/BayesianNetworkGenerator_breast-cancer_small.arff")
#dfCT = pd.read_csv("/content/drive/MyDrive/School/2223/ACI/TP/Datasets/dataset_churn_telco")
#dfH = pd.read_csv("/content/drive/MyDrive/School/2223/ACI/TP/Datasets/houses.arff")
#dfBank = pd.read_csv("/content/drive/MyDrive/School/2223/ACI/TP/Datasets/bank.arff")

dfBNG = pd.read_csv("BayesianNetworkGenerator_breast-cancer_small.arff")
dfCT = pd.read_csv("dataset_churn_telco")
dfH = pd.read_csv("houses.arff")
dfBank = pd.read_csv("bank.arff")

#BNG
for column in dfBNG.columns:
  dfBNG[column] = le.fit_transform(dfBNG[column])
dfBNG

#CT
if 'CETEL_NUMBER' in dfCT.columns and 'CNI_CUSTOMER' in dfCT.columns:
  dfCT = dfCT.drop(columns=['CETEL_NUMBER','CNI_CUSTOMER'])

dfCT = dfCT[dfCT['STATE_DATA'] != '?']
dfCT = dfCT[dfCT['CITY_DATA'] != '?']
dfCT = dfCT[dfCT['STCITY_VOICE'] != '?']
dfCT = dfCT[dfCT['TE_VOICE'] != '?']

dfCT['STATE_DATA'] = dfCT['STATE_DATA'].astype(float)
dfCT['CITY_DATA'] = dfCT['CITY_DATA'].astype(float)
dfCT['STCITY_VOICE'] = dfCT['STCITY_VOICE'].astype(float)
dfCT['TE_VOICE'] = dfCT['TE_VOICE'].astype(float)

print (dfCT.dtypes)
dfCT

#Houses
dfH['binaryClass'] = dfH['binaryClass'].apply(lambda x: 1 if x == 'P' else 0)
dfH

#BANK
for column in dfBank.columns:
  if dfBank[column].dtype == 'O':
    dfBank[column] = le.fit_transform(dfBank[column])
dfBank

dictDataFrames = {'dfBNG':{'data':dfBNG,'label':'Class'},'dfCT':{'data':dfCT,'label':'CHURN'},'dfH':{'data':dfH,'label':'binaryClass'},'dfBank':{'data':dfBank,'label':'got_term_deposit'}}

for df in dictDataFrames:
  # Identificar as features "removendo" a label
  dictDataFrames[df]['x'] = dictDataFrames[df]['data'].drop(columns = [dictDataFrames[df]['label']])
  # Define qual a label
  dictDataFrames[df]['y'] = dictDataFrames[df]['data'][dictDataFrames[df]['label']]

dictDataFrames

results = {}
for dataset in dictDataFrames:
  print (dataset)
  clf = RandomForestClassifier()
  X = dictDataFrames[dataset]['x']
  y = dictDataFrames[dataset]['y']
  cv_results = cross_validate(clf, X, y, cv=5,scoring = ['accuracy', 'precision', 'recall'])
  results[dataset] = cv_results

results

results_sample = {}
for dataset in dictDataFrames:
  print (dataset)
  clf = RandomForestClassifier(sampling_strategy = 1)
  X = dictDataFrames[dataset]['x']
  y = dictDataFrames[dataset]['y']
  cv_results = cross_validate(clf, X, y, cv=5,scoring = ['accuracy', 'precision', 'recall'])
  results_sample[dataset] = cv_results

results_sample

results_sample_12 = {}
for dataset in dictDataFrames:
  print (dataset)
  clf = RandomForestClassifier(sampling_strategy = 1.2)
  X = dictDataFrames[dataset]['x']
  y = dictDataFrames[dataset]['y']
  cv_results = cross_validate(clf, X, y, cv=5,scoring = ['accuracy', 'precision', 'recall'])
  results_sample_12[dataset] = cv_results

results_sample_08 = {}
for dataset in dictDataFrames:
  print (dataset)
  clf = RandomForestClassifier(sampling_strategy = 0.8)
  X = dictDataFrames[dataset]['x']
  y = dictDataFrames[dataset]['y']
  cv_results = cross_validate(clf, X, y, cv=5,scoring = ['accuracy', 'precision', 'recall'])
  results_sample_08[dataset] = cv_results

results_sample_12

results_sample_08

results_rf = {'dfBNG': {'fit_time': np.array([90.05252934, 86.5342679 , 89.17417002, 90.49002743, 87.30035901]),
  'score_time': np.array([6.45547128, 6.92119241, 6.47459674, 6.62053585, 6.91093254]),
  'test_accuracy': np.array([0.73722 , 0.736765, 0.736545, 0.735445, 0.7373  ]),
  'test_precision': np.array([0.54555809, 0.54498112, 0.54469066, 0.54307635, 0.54583893]),
  'test_recall': np.array([0.69308163, 0.69195437, 0.69147809, 0.69193236, 0.69070413])},
 'dfCT': {'fit_time': np.array([16.06760836, 16.61943841, 17.61923242, 16.7610836 , 17.17191863]),
  'score_time': np.array([1.0425458 , 1.34382343, 1.05754471, 1.07056522, 1.09314656]),
  'test_accuracy': np.array([0.90829305, 0.92098512, 0.93491939, 0.9282493 , 0.91466609]),
  'test_precision': np.array([0.93563833, 0.95771752, 0.99996553, 0.98206564, 0.96309528]),
  'test_recall': np.array([0.95779489, 0.94875549, 0.92332421, 0.9324591 , 0.9352621 ])},
 'dfH': {'fit_time': np.array([2.63906717, 2.74562812, 3.13201427, 3.11191297, 2.77266884]),
  'score_time': np.array([0.05794024, 0.06026578, 0.07461095, 0.05516219, 0.06086063]),
  'test_accuracy': np.array([0.95155039, 0.87960271, 0.98110465, 0.89510659, 0.92974806]),
  'test_precision': np.array([0.89914271, 1.        , 0.95808705, 1.        , 0.86003861]),
  'test_recall': np.array([1.        , 0.72125631, 1.        , 0.75715087, 1.        ])},
 'dfBank': {'fit_time': np.array([2.45743608, 2.40255618, 3.39005709, 2.35135293, 2.27360225]),
  'score_time': np.array([0.12403631, 0.14656544, 0.15208817, 0.15271592, 0.1418457 ]),
  'test_accuracy': np.array([0.74831361, 0.64786552, 0.54600752, 0.43098872, 0.28743641]),
  'test_precision': np.array([0.90391963, 0.89868793, 0.89110708, 0.929241  , 0.97298956]),
  'test_recall': np.array([0.8       , 0.67764559, 0.55348196, 0.38489479, 0.19852204])}}

results_sample_1 ={'dfBNG': {'fit_time': np.array([90.05252934, 86.5342679 , 89.17417002, 90.49002743, 87.30035901]),
  'score_time': np.array([6.45547128, 6.92119241, 6.47459674, 6.62053585, 6.91093254]),
  'test_accuracy': np.array([0.73722 , 0.736765, 0.736545, 0.735445, 0.7373  ]),
  'test_precision': np.array([0.54555809, 0.54498112, 0.54469066, 0.54307635, 0.54583893]),
  'test_recall': np.array([0.69308163, 0.69195437, 0.69147809, 0.69193236, 0.69070413])},
 'dfCT': {'fit_time': np.array([16.06760836, 16.61943841, 17.61923242, 16.7610836 , 17.17191863]),
  'score_time': np.array([1.0425458 , 1.34382343, 1.05754471, 1.07056522, 1.09314656]),
  'test_accuracy': np.array([0.90829305, 0.92098512, 0.93491939, 0.9282493 , 0.91466609]),
  'test_precision': np.array([0.93563833, 0.95771752, 0.99996553, 0.98206564, 0.96309528]),
  'test_recall': np.array([0.95779489, 0.94875549, 0.92332421, 0.9324591 , 0.9352621 ])},
 'dfH': {'fit_time': np.array([2.63906717, 2.74562812, 3.13201427, 3.11191297, 2.77266884]),
  'score_time': np.array([0.05794024, 0.06026578, 0.07461095, 0.05516219, 0.06086063]),
  'test_accuracy': np.array([0.95155039, 0.87960271, 0.98110465, 0.89510659, 0.92974806]),
  'test_precision': np.array([0.89914271, 1.        , 0.95808705, 1.        , 0.86003861]),
  'test_recall': np.array([1.        , 0.72125631, 1.        , 0.75715087, 1.        ])},
 'dfBank': {'fit_time': np.array([2.45743608, 2.40255618, 3.39005709, 2.35135293, 2.27360225]),
  'score_time': np.array([0.12403631, 0.14656544, 0.15208817, 0.15271592, 0.1418457 ]),
  'test_accuracy': np.array([0.74831361, 0.64786552, 0.54600752, 0.43098872, 0.28743641]),
  'test_precision': np.array([0.90391963, 0.89868793, 0.89110708, 0.929241  , 0.97298956]),
  'test_recall': np.array([0.8       , 0.67764559, 0.55348196, 0.38489479, 0.19852204])}}

results_sample_08 = {'dfBNG': {'fit_time': np.array([102.30074501, 100.47995114,  99.70886803,  98.50672436,
          93.37410545]),
  'score_time': np.array([6.66170454, 6.33972645, 6.68046403, 6.21667647, 6.23846531]),
  'test_accuracy': np.array([0.75655 , 0.75571 , 0.75561 , 0.755185, 0.757145]),
  'test_precision': np.array([0.58373072, 0.58210948, 0.58237488, 0.5815678 , 0.58486704]),
  'test_recall': np.array([0.63022411, 0.63086345, 0.6278792 , 0.62811475, 0.62984773])},
 'dfCT': {'fit_time': np.array([18.56377435, 18.39930439, 20.01186466, 19.41224694, 17.96119952]),
  'score_time': np.array([1.04773927, 1.19437408, 1.05790758, 1.08066344, 1.0810864 ]),
  'test_accuracy': np.array([0.91066944, 0.9269801 , 0.94842159, 0.93948314, 0.92311847]),
  'test_precision': np.array([0.93108208, 0.95496894, 0.99996611, 0.97974284, 0.95974126]),
  'test_recall': np.array([0.96622955, 0.95916354, 0.93923865, 0.94827806, 0.94920271])},
 'dfH': {'fit_time': np.array([3.23388553, 3.64120054, 3.03772831, 2.79974318, 3.1687448 ]),
  'score_time': np.array([0.07468486, 0.06234217, 0.06310821, 0.0557394 , 0.08998942]),
  'test_accuracy': np.array([0.95373062, 0.86918605, 0.98401163, 0.89510659, 0.9314438 ]),
  'test_precision': np.array([0.90324215, 1.        , 0.96430503, 1.        , 0.862954  ]),
  'test_recall': np.array([1.        , 0.69713965, 1.        , 0.75715087, 1.        ])},
 'dfBank': {'fit_time': np.array([3.29768181, 2.55554342, 2.58179712, 2.55947328, 3.39816189]),
  'score_time': np.array([0.12114263, 0.14452624, 0.1492002 , 0.14638805, 0.14046335]),
  'test_accuracy': np.array([0.77916621, 0.64985623, 0.5731033 , 0.43685025, 0.29329794]),
  'test_precision': np.array([0.89951962, 0.8889427 , 0.8890566 , 0.9188876 , 0.97159763]),
  'test_recall': np.array([0.84420789, 0.68966813, 0.59018036, 0.39729459, 0.20566132])}}

results_sample_12 = {'dfBNG': {'fit_time': np.array([100.69348025,  80.57174516,  84.61720896,  98.00122142,
          94.51621509]),
  'score_time': np.array([6.23524594, 6.17605257, 7.39019656, 7.65200853, 8.10877991]),
  'test_accuracy': np.array([0.71729 , 0.71351 , 0.7163  , 0.714695, 0.71553 ]),
  'test_precision': np.array([0.51703957, 0.51241118, 0.51578405, 0.51383353, 0.51488629]),
  'test_recall': np.array([0.73872737, 0.74256343, 0.74085976, 0.74181879, 0.73936233])},
 'dfCT': {'fit_time': np.array([15.9701736 , 17.56066155, 16.88909006, 17.38255525, 15.69920301]),
  'score_time': np.array([1.17193317, 1.20746064, 1.16813231, 1.33254433, 1.09477329]),
  'test_accuracy': np.array([0.9030542 , 0.91204666, 0.92049904, 0.91628635, 0.90634873]),
  'test_precision': np.array([0.93937097, 0.96013202, 0.99996488, 0.98390294, 0.96567925]),
  'test_recall': np.array([0.94684576, 0.93516456, 0.90632758, 0.91632185, 0.92240364])},
 'dfH': {'fit_time': np.array([2.65437269, 2.6670866 , 3.70813632, 2.64968443, 2.71072078]),
  'score_time': np.array([0.06028295, 0.07124472, 0.07005811, 0.06133509, 0.06566238]),
  'test_accuracy': np.array([0.94694767, 0.88517442, 0.97843992, 0.89510659, 0.92853682]),
  'test_precision': np.array([0.89060939, 1.        , 0.95245726, 1.        , 0.85796822]),
  'test_recall': np.array([1.        , 0.73415592, 1.        , 0.75715087, 1.        ])},
 'dfBank': {'fit_time': np.array([2.54845095, 2.90273404, 2.69197106, 2.23318624, 2.24527764]),
  'score_time': np.array([0.13109899, 0.17853355, 0.15452218, 0.16601324, 0.1421926 ]),
  'test_accuracy': np.array([0.72951454, 0.6305021 , 0.52455209, 0.40643663, 0.26487503]),
  'test_precision': np.array([0.90962875, 0.90298507, 0.89700496, 0.93573094, 0.97377746]),
  'test_recall': np.array([0.77019411, 0.65159674, 0.52141784, 0.35195391, 0.17209419])}}

for dataset in results_sample_08:
  results_sample_08[dataset]['precision'] = np.mean(results_sample_08[dataset]['test_precision'])
  results_sample_08[dataset]['accuracy'] = np.mean(results_sample_08[dataset]['test_accuracy'])
  results_sample_08[dataset]['recall'] = np.mean(results_sample_08[dataset]['test_recall'])

for dataset in results_sample_1:
  results_sample_1[dataset]['precision'] = np.mean(results_sample_1[dataset]['test_precision'])
  results_sample_1[dataset]['accuracy'] = np.mean(results_sample_1[dataset]['test_accuracy'])
  results_sample_1[dataset]['recall'] = np.mean(results_sample_1[dataset]['test_recall'])

for dataset in results_rf:
  results_rf[dataset]['precision'] = np.mean(results_rf[dataset]['test_precision'])
  results_rf[dataset]['accuracy'] = np.mean(results_rf[dataset]['test_accuracy'])
  results_rf[dataset]['recall'] = np.mean(results_rf[dataset]['test_recall'])

for dataset in results_sample_12:
  results_sample_12[dataset]['precision'] = np.mean(results_sample_12[dataset]['test_precision'])
  results_sample_12[dataset]['accuracy'] = np.mean(results_sample_12[dataset]['test_accuracy'])
  results_sample_12[dataset]['recall'] = np.mean(results_sample_12[dataset]['test_recall'])

import matplotlib.pyplot as plt
for dataset in results_rf:
  for metric in ['precision', 'recall', 'accuracy']:
    print(dataset, metric)
    plt.barh(
        y = ['Random Forest', 'sampling strategy = 1', 'sampling strategy = 0.8'],
        width = [results_rf[dataset][metric], results_sample_1[dataset][metric], results_sample_08[dataset][metric]]
    )
    plt.xlim(0.5,1)
    plt.show()

import seaborn as sns
import matplotlib.pyplot as plt

# learning algs
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.svm import SVC

# experimental study
from sklearn.model_selection import KFold, cross_val_score
from sklearn.metrics import accuracy_score

# statistical tests
import scipy.stats as ss

# Learning Alg.
algs = [
 ('rf_1' , RandomForestClassifier(sampling_strategy = 1)),
 ('rf_08' , RandomForestClassifier(sampling_strategy = 0.8)),
 ('knn', KNeighborsClassifier()),
 ('dt', DecisionTreeClassifier()),
 ('lda', LinearDiscriminantAnalysis()),
 ('linear_svc', SVC(kernel="linear")),
 ('poly_svc', SVC(kernel="poly")),
]

results_KN = {}
for dataset in dictDataFrames:
  print (dataset)
  clf = KNeighborsClassifier()
  X = dictDataFrames[dataset]['x']
  y = dictDataFrames[dataset]['y']
  cv_results = cross_validate(clf, X, y, cv=5,scoring = ['accuracy', 'precision', 'recall'])
  results_KN[dataset] = cv_results

results_KN

results_KN = {'dfBNG': {'fit_time': np.array([2.34540725, 2.32600498, 2.30816531, 2.52211547, 3.45021224]),
  'score_time': np.array([105.80876017, 105.12909484, 104.14520192, 103.4566164 ,
         103.86129093]),
  'test_accuracy':np.array([0.757725, 0.75765 , 0.757085, 0.759155, 0.75774 ]),
  'test_precision': np.array([0.61732088, 0.61537814, 0.61526043, 0.6225631 , 0.61993579]),
  'test_recall': np.array([0.48606905, 0.49202504, 0.48732228, 0.48141667, 0.47758055])},
 'dfCT': {'fit_time': np.array([0.03042245, 0.03486776, 0.04369378, 0.03937006, 0.02923918]),
  'score_time': np.array([31.525172  , 32.00146031, 32.39881444, 31.48198485, 33.63549304]),
  'test_accuracy': np.array([0.89414275, 0.90205503, 0.91814966, 0.92255138, 0.91690746]),
  'test_precision': np.array([0.90416838, 0.91374125, 0.92975444, 0.93367967, 0.92981498]),
  'test_recall': np.array([0.97899293, 0.97676491, 0.97736966, 0.97819721, 0.97571533])},
 'dfH': {'fit_time': np.array([0.02114916, 0.02081943, 0.02122784, 0.02093649, 0.02118301]),
  'score_time': np.array([0.11435914, 0.12200022, 0.12739062, 0.11406898, 0.11496401]),
  'test_accuracy': np.array([0.53221899, 0.59060078, 0.57025194, 0.44016473, 0.53900194]),
  'test_precision': np.array([0.45498783, 0.5301361 , 0.50271575, 0.33803681, 0.4688946 ]),
  'test_recall': np.array([0.41951767, 0.45877734, 0.46719013, 0.30902973, 0.51178451])}}

for dataset in results_KN:
  results_KN[dataset]['precision'] = np.mean(results_KN[dataset]['test_precision'])
  results_KN[dataset]['accuracy'] = np.mean(results_KN[dataset]['test_accuracy'])
  results_KN[dataset]['recall'] = np.mean(results_KN[dataset]['test_recall'])

results_DT = {}
for dataset in dictDataFrames:
  print (dataset)
  clf = DecisionTreeClassifier()
  X = dictDataFrames[dataset]['x']
  y = dictDataFrames[dataset]['y']
  cv_results = cross_validate(clf, X, y, cv=5,scoring = ['accuracy', 'precision', 'recall'])
  results_DT[dataset] = cv_results

results_DT

results_DT = {'dfBNG': {'fit_time': np.array([2.29302979, 2.16867828, 2.1860795 , 2.9407053 , 2.57747912]),
  'score_time': np.array([0.21811819, 0.22161269, 0.21809411, 0.32215524, 0.21876025]),
  'test_accuracy': np.array([0.77957 , 0.778205, 0.777845, 0.777655, 0.778215]),
  'test_precision': np.array([0.70139071, 0.69810527, 0.69614097, 0.6989842 , 0.70083649]),
  'test_recall': np.array([0.44972744, 0.44695134, 0.44798519, 0.44226466, 0.44263481])},
 'dfCT': {'fit_time': np.array([1.81176853, 1.87777829, 1.94832826, 1.89558053, 2.49495149]),
  'score_time': np.array([0.03865051, 0.0366888 , 0.03698897, 0.03659773, 0.05971718]),
  'test_accuracy': np.array([0.89535794, 0.9122897 , 0.95098701, 0.92892441, 0.90629473]),
  'test_precision': np.array([0.91804025, 0.94446198, 0.99962869, 0.97011367, 0.94165798]),
  'test_recall': np.array([0.96260106, 0.95263861, 0.94258069, 0.9453498 , 0.94831153])},
 'dfH': {'fit_time': np.array([0.09238291, 0.07520151, 0.07041335, 0.09289074, 0.07694054]),
  'score_time': np.array([0.01112652, 0.01109958, 0.01136136, 0.01103282, 0.01378131]),
  'test_accuracy': np.array([0.94937016, 0.83745155, 0.97747093, 0.89171512, 0.92659884]),
  'test_precision': np.array([0.89747475, 1.        , 0.95090715, 1.        , 0.85915493]),
  'test_recall': np.array([0.99663489, 0.62366798, 0.99943915, 0.74929893, 0.99270483])}}

for dataset in results_DT:
  results_DT[dataset]['precision'] = np.mean(results_DT[dataset]['test_precision'])
  results_DT[dataset]['accuracy'] = np.mean(results_DT[dataset]['test_accuracy'])
  results_DT[dataset]['recall'] = np.mean(results_DT[dataset]['test_recall'])

results_LD = {}
for dataset in dictDataFrames:
  print (dataset)
  clf = LinearDiscriminantAnalysis()
  X = dictDataFrames[dataset]['x']
  y = dictDataFrames[dataset]['y']
  cv_results = cross_validate(clf, X, y, cv=5,scoring = ['accuracy', 'precision', 'recall'])
  results_LD[dataset] = cv_results

results_LD

results_LD = {'dfBNG': {'fit_time': np.array([1.53177404, 2.45571351, 1.98888946, 1.34226561, 1.33886552]),
  'score_time': np.array([0.33363223, 0.33381987, 0.2485466 , 0.21996212, 0.2207942 ]),
  'test_accuracy': np.array([0.750015, 0.749585, 0.747995, 0.74969 , 0.75059 ]),
  'test_precision': np.array([0.63527186, 0.63397026, 0.62968705, 0.63500706, 0.63783004]),
  'test_recall': np.array([0.37290531, 0.37233327, 0.36900816, 0.37087575, 0.37190208])},
 'dfCT': {'fit_time': np.array([0.45697165, 0.4454236 , 0.48505831, 0.46070027, 0.47331309]),
  'score_time': np.array([0.08858514, 0.04175854, 0.04143381, 0.04464769, 0.04321766]),
  'test_accuracy': np.array([0.84855932, 0.88174772, 0.94388485, 0.89033512, 0.8484513 ]),
  'test_precision': np.array([0.84857652, 0.87786132, 0.93838154, 0.88573362, 0.84850777]),
  'test_recall': np.array([0.99993634, 0.99971354, 0.99949074, 0.99971354, 0.99990452])},
 'dfH': {'fit_time': np.array([0.04432082, 0.0412693 , 0.04175258, 0.07475424, 0.08900189]),
  'score_time': np.array([0.01134348, 0.01073194, 0.01022553, 0.01960707, 0.01051235]),
  'test_accuracy': np.array([0.97892442, 0.9869186 , 0.99467054, 0.89510659, 0.95203488]),
  'test_precision': np.array([0.95347594, 1.        , 0.98781163, 1.        , 0.9       ]),
  'test_recall': np.array([1.        , 0.96971397, 1.        , 0.75715087, 1.        ])}}

for dataset in results_LD:
  results_LD[dataset]['precision'] = np.mean(results_LD[dataset]['test_precision'])
  results_LD[dataset]['accuracy'] = np.mean(results_LD[dataset]['test_accuracy'])
  results_LD[dataset]['recall'] = np.mean(results_LD[dataset]['test_recall'])