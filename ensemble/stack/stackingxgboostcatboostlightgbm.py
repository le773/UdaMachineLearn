# Stacking Starter based on Allstate Faron's Script
#https://www.kaggle.com/mmueller/allstate-claims-severity/stacking-starter/run/390867
# Preprocessing from ogrellier
#https://www.kaggle.com/ogrellier/good-fun-with-ligthgbm

import pandas as pd
import numpy as np
from scipy.stats import skew
import xgboost as xgb
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import mean_squared_error
from sklearn.linear_model import LogisticRegression
from math import sqrt
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import KFold, cross_val_score
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier
import gc

NFOLDS = 3
SEED = 0
NROWS = None

print('load data ...')

data = pd.read_csv('./input/HomeCreditDefaultRisk/application_train.csv')
test = pd.read_csv('./input/HomeCreditDefaultRisk/application_test.csv')
prev = pd.read_csv('./input/HomeCreditDefaultRisk/previous_application.csv')

# print('%-55s | %7s | %10s | %10s | %10s'
#       % ('FEATURES', 'TYPE', 'NB VALUES', 'NB NaNS', 'NaNs (%)'))
# for f_ in data: # .dtypes
#     print("%-55s | %7s | %10s | %10s |    %5.2f"
#           % (f_, str(data[f_].dtype),
#              str(len(data[f_].value_counts(dropna=False))),
#              str(data[f_].isnull().sum()),
#              100 * data[f_].isnull().sum() / data.shape[0]
#             )
#          )

categorical_feats = [
    f for f in data.columns if data[f].dtype == 'object'
]

for f_ in categorical_feats:
    data[f_], indexer = pd.factorize(data[f_])
    test[f_] = indexer.get_indexer(test[f_])

gc.enable()

y_train = data['TARGET']
del data['TARGET']

###################################
# PLEASE DON'T DO THIS AT HOME LOL
# Averaging factorized categorical features defeats my own reasoning
###################################
prev_cat_features = [
    f_ for f_ in prev.columns if prev[f_].dtype == 'object'
]
for f_ in prev_cat_features:
    prev[f_], _ = pd.factorize(prev[f_])

avg_prev = prev.groupby('SK_ID_CURR').mean()
cnt_prev = prev[['SK_ID_CURR', 'SK_ID_PREV']].groupby('SK_ID_CURR').count()

print('cnt_prev head ...')
print(cnt_prev.head())

avg_prev['nb_app'] = cnt_prev['SK_ID_PREV']
del avg_prev['SK_ID_PREV']

print('merge ... ')
x_train = data.merge(right=avg_prev.reset_index(), how='left', on='SK_ID_CURR')
x_test = test.merge(right=avg_prev.reset_index(), how='left', on='SK_ID_CURR')

x_train = x_train.fillna(0)
x_test= x_test.fillna(0)

ntrain = x_train.shape[0]
ntest = x_test.shape[0]

excluded_feats = ['SK_ID_CURR']
features = [f_ for f_ in x_train.columns if f_ not in excluded_feats]

x_train = x_train[features]
x_test = x_test[features]

kf = KFold(n_splits = NFOLDS, shuffle=True, random_state=SEED)

class SklearnWrapper(object):
    def __init__(self, clf, seed=0, params=None):
        params['random_state'] = seed
        self.clf = clf(**params)

    def train(self, x_train, y_train):
        self.clf.fit(x_train, y_train)

    def predict(self, x):
        return self.clf.predict_proba(x)[:,1]

class CatboostWrapper(object):
    def __init__(self, clf, seed=0, params=None):
        params['random_seed'] = seed
        self.clf = clf(**params)

    def train(self, x_train, y_train):
        self.clf.fit(x_train, y_train)

    def predict(self, x):
        return self.clf.predict_proba(x)[:,1]

class LightGBMWrapper(object):
    def __init__(self, clf, seed=0, params=None):
        params['feature_fraction_seed'] = seed
        params['bagging_seed'] = seed
        self.clf = clf(**params)

    def train(self, x_train, y_train):
        self.clf.fit(x_train, y_train)

    def predict(self, x):
        return self.clf.predict_proba(x)[:,1]


class XgbWrapper(object):
    def __init__(self, seed=0, params=None):
        self.param = params
        self.param['seed'] = seed
        self.nrounds = params.pop('nrounds', 250)

    def train(self, x_train, y_train):
        dtrain = xgb.DMatrix(x_train, label=y_train)
        self.gbdt = xgb.train(self.param, dtrain, self.nrounds)

    def predict(self, x):
        return self.gbdt.predict(xgb.DMatrix(x))


def get_oof(clf):
    oof_train = np.zeros((ntrain,))
    oof_test = np.zeros((ntest,))
    oof_test_skf = np.empty((NFOLDS, ntest))

    for i, (train_index, test_index) in enumerate(kf.split(x_train)):
        x_tr = x_train.loc[train_index]
        y_tr = y_train.loc[train_index]
        x_te = x_train.loc[test_index]

        clf.train(x_tr, y_tr)

        oof_train[test_index] = clf.predict(x_te)
        oof_test_skf[i, :] = clf.predict(x_test)

    oof_test[:] = oof_test_skf.mean(axis=0)
    return oof_train.reshape(-1, 1), oof_test.reshape(-1, 1)


et_params = {
    'n_jobs': 8,
    'n_estimators': 200,
    'max_features': 0.5,
    'max_depth': 12,
    'min_samples_leaf': 2,
}

rf_params = {
    'n_jobs': 8,
    'n_estimators': 200,
    'max_features': 0.2,
    'max_depth': 12,
    'min_samples_leaf': 2,
}

xgb_params = {
    'seed': 0,
    'colsample_bytree': 0.7,
    'silent': 1,
    'subsample': 0.7,
    'learning_rate': 0.075,
    'objective': 'binary:logistic',
    'max_depth': 4,
    'num_parallel_tree': 1,
    'min_child_weight': 1,
    'nrounds': 200
}

catboost_params = {
    'iterations': 200,
    'learning_rate': 0.5,
    'depth': 3,
    'l2_leaf_reg': 40,
    'bootstrap_type': 'Bernoulli',
    'subsample': 0.7,
    'scale_pos_weight': 5,
    'eval_metric': 'AUC',
    'od_type': 'Iter',
    'allow_writing_files': False
}

lightgbm_params = {
    'n_estimators':200,
    'learning_rate':0.1,
    'num_leaves':123,
    'colsample_bytree':0.8,
    'subsample':0.9,
    'max_depth':15,
    'reg_alpha':0.1,
    'reg_lambda':0.1,
    'min_split_gain':0.01,
    'min_child_weight':2
}


print('XgbWrapper ...')
xg = XgbWrapper(seed=SEED, params=xgb_params)
xg_oof_train, xg_oof_test = get_oof(xg)

print('ExtraTreesClassifier ...')
et = SklearnWrapper(clf=ExtraTreesClassifier, seed=SEED, params=et_params)
et_oof_train, et_oof_test = get_oof(et)

print('RandomForestClassifier ...')
rf = SklearnWrapper(clf=RandomForestClassifier, seed=SEED, params=rf_params)
rf_oof_train, rf_oof_test = get_oof(rf)

print('CatBoostClassifier ...')
cb = CatboostWrapper(clf= CatBoostClassifier, seed = SEED, params=catboost_params)
cb_oof_train, cb_oof_test = get_oof(cb)

print('LGBMClassifier ...')
lg = LightGBMWrapper(clf = LGBMClassifier, seed = SEED, params = lightgbm_params)


print("XG-CV: {}".format(sqrt(mean_squared_error(y_train, xg_oof_train))))
print("ET-CV: {}".format(sqrt(mean_squared_error(y_train, et_oof_train))))
print("RF-CV: {}".format(sqrt(mean_squared_error(y_train, rf_oof_train))))
print("RF-CV: {}".format(sqrt(mean_squared_error(y_train, cb_oof_train))))

x_train = np.concatenate((xg_oof_train, et_oof_train, rf_oof_train, cb_oof_train), axis=1)
x_test = np.concatenate((xg_oof_test, et_oof_test, rf_oof_test, cb_oof_test), axis=1)

print("{},{}".format(x_train.shape, x_test.shape))

print('LogisticRegression ...')
logistic_regression = LogisticRegression()
logistic_regression.fit(x_train,y_train)

test['TARGET'] = logistic_regression.predict_proba(x_test)[:,1]

test[['SK_ID_CURR', 'TARGET']].to_csv('first_submission.csv', index=False, float_format='%.8f')

results = cross_val_score(xg, x_train, x_test, cv=3, scoring='r2')
print("xg score: %.4f (%.4f)" % (results.mean(), results.std()))

results = cross_val_scoree(et, x_train, x_test, cv=3, scoring='r2')
print("et score: %.4f (%.4f)" % (results.mean(), results.std()))

results = cross_val_score(rf, x_train, x_test, cv=3, scoring='r2')
print("rf score: %.4f (%.4f)" % (results.mean(), results.std()))

results = cross_val_score(cb, x_train, x_test, cv=3, scoring='r2')
print("cb score: %.4f (%.4f)" % (results.mean(), results.std()))

results = cross_val_score(lg, x_train, x_test, cv=3, scoring='r2')
print("lg score: %.4f (%.4f)" % (results.mean(), results.std()))