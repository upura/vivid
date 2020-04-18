import os
import shutil
from unittest import TestCase

import numpy as np
import pandas as pd
import pytest
from sklearn.datasets import load_boston, load_breast_cancer
from sklearn.metrics import make_scorer
from sklearn.metrics import mean_absolute_error, mean_squared_log_error

from tests.conftest import SampleFeature, RecordingFeature, RECORDING_DIR
from vivid.out_of_fold import boosting
from vivid.out_of_fold.base import NotFittedError
from vivid.out_of_fold.ensumble import RFRegressorFeatureOutOfFold
from vivid.out_of_fold.kneighbor import OptunaKNeighborRegressorOutOfFold

base_feat = SampleFeature()


def get_boston():
    X, y = load_boston(True)
    return pd.DataFrame(X), y


def get_binary():
    x, y = load_breast_cancer(True)
    return pd.DataFrame(x), y


class TestCore(TestCase):
    def setUp(self):
        if os.path.exists(RECORDING_DIR):
            shutil.rmtree(RECORDING_DIR)

    def test_serializing(self):
        feat_none_save = OptunaKNeighborRegressorOutOfFold(name='serialize_0', n_trials=1)
        feat_none_save.fit(*get_boston())

        with pytest.raises(NotFittedError):
            feat_none_save.load_best_models()

    def test_not_recoding(self):
        feat_not_recoding_root = OptunaKNeighborRegressorOutOfFold(parent=base_feat, name='not_save_parent', n_trials=1)
        feat_not_recoding_root.fit(*get_boston())
        with pytest.raises(NotFittedError):
            feat_not_recoding_root.load_best_models()

    def test_use_cache(self):
        df, y = get_boston()
        feat = OptunaKNeighborRegressorOutOfFold(parent=None, name='knn', n_trials=1)

        output_df = feat.fit(df, y)
        self.assertIsNotNone(feat.feat_on_train_df, feat)
        output_repeat_df = feat.fit(df, y)
        self.assertIs(output_df, output_repeat_df, feat)

    def test_recording_with_feature(self):
        recording_feature = RecordingFeature()
        feat = OptunaKNeighborRegressorOutOfFold(parent=recording_feature, name='serialize_1', n_trials=1)

        with pytest.raises(NotFittedError):
            feat.load_best_models()

        # 学習前なのでモデルの重みはあってはならない
        self.assertFalse(os.path.exists(feat.serializer_path), feat)
        feat.fit(*get_boston())

        self.assertTrue(feat.is_recording, feat)
        self.assertTrue(feat.finish_fit, feat)

        # 学習後なのでモデルの重みが無いとだめ
        self.assertTrue(os.path.exists(feat.serializer_path), feat)

        # モデル読み込みと予測が可能
        feat.load_best_models()
        feat.predict(get_boston()[0])

    def test_add_sample_weight(self):
        df, y = get_boston()
        sample_weight = df.values[:, 0]
        model = RFRegressorFeatureOutOfFold(name='rf', sample_weight=sample_weight)
        model.fit(df, y)

        for clf, (idx_train, idx_valid) in zip(model.fitted_models, model.get_fold_splitting(df.values, y)):
            assert np.array_equal(clf.fit_params_.get('sample_weight', None), sample_weight[idx_train])


@pytest.mark.parametrize('metric_func', [
    mean_absolute_error, mean_squared_log_error
])
def test_optuna_change_metric(metric_func):
    """metric を別のものに変えた時に正常に動作するか"""

    df, y = get_boston()
    scoring = make_scorer(metric_func, greater_is_better=False)
    model = OptunaKNeighborRegressorOutOfFold(name='optuna', n_trials=1, scoring=scoring,
                                              scoring_strategy='fold')
    model.fit(df, y)

    X = df.values
    scores = []
    for clf, (idx_train, idx_valid) in zip(model.fitted_models, model.get_fold_splitting(X, y)):
        pred_i = clf.predict(X[idx_valid])
        score = metric_func(y[idx_valid], pred_i)
        scores.append(score)
    score = np.mean(scores)
    np.testing.assert_almost_equal(-score, model.study.best_value, decimal=7)


def test_change_scoring_strategy():
    """check same scoring value in convex objective"""
    df, y = get_boston()
    model = OptunaKNeighborRegressorOutOfFold(name='test', n_trials=1,
                                              scoring='neg_root_mean_squared_error',
                                              scoring_strategy='whole')
    oof_df = model.fit(df, y)
    from sklearn.metrics import mean_squared_error

    assert - mean_squared_error(y, oof_df.values[:, 0]) ** .5 == model.study.best_value


def test_optuna_min_max_shift():
    df, y = get_boston()
    scoring = make_scorer(mean_absolute_error, greater_is_better=False)
    model = OptunaKNeighborRegressorOutOfFold(name='optuna', n_trials=10, scoring=scoring)
    model.fit(df, y)
    log_df = model.study.trials_dataframe()
    assert (log_df['value'] > 0).sum() == 0, log_df


class TestKneighbors(object):
    def test_optuna(self):
        df_train, y = get_boston()
        feat = OptunaKNeighborRegressorOutOfFold(parent=None, name='test_kn_1', n_trials=1)
        feat.fit(df_train, y, force=True)

        feat = OptunaKNeighborRegressorOutOfFold(parent=base_feat, name='test_kn_1', n_trials=1)
        feat.fit(df_train, y, force=True)
        assert feat.study is not None
        assert isinstance(feat.study.trials_dataframe(), pd.DataFrame)


class TestXGBoost(object):
    def test_xgb_feature(self, train_data):
        train_df, y = train_data
        feat = boosting.XGBoostRegressorOutOfFold(parent=None, name='test_xgb')
        feat.fit(train_df, y, force=True)

    def test_optuna_xgb(self, train_data):
        feat = boosting.OptunaXGBRegressionOutOfFold(parent=None, name='test_xgb_optuna', n_trials=1)
        df_train, y = get_boston()
        feat.fit(df_train, y, force=True)

        assert feat.study.best_params is not None
        assert feat.study.best_value is not None
        assert feat.study is not None
        assert isinstance(feat.study.trials_dataframe(), pd.DataFrame)
        pred = feat.predict(df_train)
        assert pred.values.shape[0] == y.shape[0]


class TestLightGBM(object):
    def test_lgbm_feature(self, train_data):
        train_df, y = train_data
        feat = boosting.LGBMRegressorOutOfFold(parent=None, name='test_lightgbm')
        feat.fit(train_df, y, force=True)
