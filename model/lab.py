import pdb
import time
import typing
import pandas as pd
from xgboost import XGBRegressor
from skopt import BayesSearchCV
from sklearn.linear_model import LinearRegression
from sklearn.decomposition import PCA
from sklearn.metrics import r2_score, mean_squared_error
import os
from dateutil.relativedelta import relativedelta
import numpy as np
import copy
from data_centre.helpers import coin_ls
from data_centre.data import Reader
import concurrent.futures
from hottbox.pdtools import pd_to_tensor
from itertools import product
from scipy.stats import t
import sqlite3
from model.feature_engineering_room import FeatureAR, FeatureRiskMetricsEstimator, FeatureHAR,\
    FeatureHARDummy, FeatureHARCDR, FeatureHARCSR, FeatureHARUniversal, FeatureHARUniversalPuzzle, rv_1w_correction
import argparse
import matplotlib.pyplot as plt
from datetime import datetime


"""Functions used to facilitate computation within classes."""
qlike_score = lambda x: ((x.iloc[:, 0].div(x.iloc[:, -1]))-np.log(x.iloc[:, 0].div(x.iloc[:, -1]))-1).mean()


class ModelBuilder:

    _factory_model_type_dd = {'ar': FeatureAR(),
                              'risk_metrics': FeatureRiskMetricsEstimator(),
                              'har': FeatureHAR(),
                              'har_dummy_markets': FeatureHARDummy(),
                              'har_cdr': FeatureHARCDR(),
                              'har_csr': FeatureHARCSR(),
                              'har_universal': {False: FeatureHARUniversal(), True: FeatureHARUniversalPuzzle()}}
    _params_grid_dd = {'n_estimators': [100, 500, 1_000],
                       'lambda': np.linspace(0, 1, 11).tolist(),
                       'max_depth': [int(depth) for depth in np.linspace(1, 6, 6).tolist()],
                       'eta': np.linspace(0, 1, 11).tolist()}
    _factory_regression_dd = {'linear': LinearRegression(),
                              'ensemble': BayesSearchCV(estimator=XGBRegressor(objective='reg:squarederror'),
                                                        search_spaces=_params_grid_dd)}
    _pca_obj = PCA()
    _factory_transformation_dd = {'log': {'transformation': np.log, 'inverse': np.exp},
                                  None: {'transformation': lambda x: x, 'inverse': lambda x: x},
                                  'shift': {'transformation': lambda x: x+1,
                                             'inverse': lambda x: x-1}}
    models = [None, 'ar', 'risk_metrics', 'har', 'har_dummy_markets', 'har_cdr', 'har_csr', 'har_universal']
    models_rolling_metrics_dd = {model: dict([('qlike', {}), ('r2', {}), ('mse', {}),
                                              ('tstats', {}), ('pvalues', {}), ('coefficient', {})])
                                 for _, model in enumerate(models) if model}
    models_forecast_dd = {model: list() for _, model in enumerate(models) if model}
    coins = coin_ls
    global coins_copy
    coins = [''.join((coin, 'usdt')).upper() for _, coin in enumerate(coins)]
    coins_copy = coins[::]
    _ensemble_model_store_dd = dict([(model, dict()) for model in models])
    global _pca_components_symbols_dd
    _pca_components_symbols_dd = dict([(coin, list()) for coin in coins_copy])
    pca_components_models_dd = dict([(model, _pca_components_symbols_dd) for model in models
                                     if model not in [None, 'ar', 'har_universal', 'risk_metrics']])
    pca_components_components_dd = dict([(model, dict([(coin, None) for coin in coins_copy])) for model in models if
                                         model not in [None, 'ar', 'har_universal', 'risk_metrics']])
    global symbol_networks_dd
    symbol_networks_dd = dict([(symbol, None) for _, symbol in enumerate(coins_copy)])
    models_networks_dd = \
        dict([(model, symbol_networks_dd) for _, model in enumerate(models) if model not in [None, 'har_universal']])
    outliers_dd = {L: list() for _, L in enumerate(['1H', '6H', '12H', '1D', '1W', '1M'])}
    _pairs = list(product(coins, repeat=2))
    _pairs = [(syms[0], syms[-1]) for _, syms in enumerate(_pairs)]
    L_shift_dd = {'5T': pd.to_timedelta('5T') // pd.to_timedelta('5T'),
                  '30T': pd.to_timedelta('30T') // pd.to_timedelta('5T'),
                  '1H': pd.to_timedelta('1H') // pd.to_timedelta('5T'),
                  '6H': pd.to_timedelta('6H') // pd.to_timedelta('5T'),
                  '12H': pd.to_timedelta('12H') // pd.to_timedelta('5T'),
                  '1D': pd.to_timedelta('1D') // pd.to_timedelta('5T'),
                  '1W': pd.to_timedelta('1W') // pd.to_timedelta('5T'),
                  '1M': pd.to_timedelta('30D') // pd.to_timedelta('5T')}
    start_dd = {'1D': relativedelta(days=1), '1W': relativedelta(weeks=1), '1M': relativedelta(months=1)}
    db_connect_coefficient = \
        sqlite3.connect(database=os.path.abspath('./data_centre/databases/coefficients.db'))
    db_connect_mse = sqlite3.connect(database=os.path.abspath('./data_centre/databases/mse.db'))
    db_connect_qlike = sqlite3.connect(database=os.path.abspath('./data_centre/databases/qlike.db'))
    db_connect_r2 = sqlite3.connect(database=os.path.abspath('./data_centre/databases/r2.db'))
    db_connect_y = sqlite3.connect(database=os.path.abspath('./data_centre/databases/y.db'))
    db_connect_correlation = sqlite3.connect(database=os.path.abspath('./data_centre/databases/correlation.db'))
    db_connect_outliers = sqlite3.connect(database=os.path.abspath('./data_centre/databases/outliers.db'))
    db_connect_pca = sqlite3.connect(database=os.path.abspath('./data_centre/databases/pca.db'))
    reader_obj = Reader(file='./data_centre/tmp/aggregate2022')

    def __init__(self, h: str, F: typing.List[str], L: str, Q: str, model_type: str=None, s=None, b: str='5T'):
        """
            h: Forecasting horizon
            s: Sliding window
            F: Feature building lookback
            L: Lookback window for model training
            Q: Model update frequency
        """
        self._s = h if s is None else s
        self._h = h
        self._F = F
        self._L = L
        self._Q = Q
        self._b = b
        self._L = L
        max_feature_building_lookback = \
            max([pd.to_timedelta(lookback) // pd.to_timedelta(self._b) if 'M' not in lookback else
                 pd.to_timedelta(''.join((str(30 * int(lookback.split('M')[0])), 'D'))) // pd.to_timedelta(self._b)
                 for _, lookback in enumerate(self._F)])
        upper_bound_feature_building_lookback = \
            pd.to_timedelta(''.join((str(30 * int(self.L.split('M')[0])), 'D'))) // pd.to_timedelta(self._b) \
                if 'M' in self._L else pd.to_timedelta(self._L) // pd.to_timedelta(self._b)
        if upper_bound_feature_building_lookback < max_feature_building_lookback:
            raise ValueError('Lookback window for model training is smaller than the furthest lookback window '
                             'in feature building.')
        if model_type not in ModelBuilder.models:
            raise ValueError('Unknown model type.')
        else:
            self._model_type = model_type

    @property
    def s(self):
        return self._s

    @property
    def h(self):
        return self._h

    @property
    def F(self):
        return self._F

    @property
    def L(self):
        return self._L

    @property
    def Q(self):
        return self._Q

    @property
    def model_type(self):
        return self._model_type

    @s.setter
    def s(self, s: str):
        self._s = s

    @h.setter
    def h(self, h: str):
        self._h = h

    @F.setter
    def F(self, F: typing.List[str]):
        max_feature_building_lookback = \
            max([pd.to_timedelta(lookback) // pd.to_timedelta(self._b) if 'M' not in lookback else
                 pd.to_timedelta(''.join((str(30 * int(lookback.split('M')[0])), 'D'))) // pd.to_timedelta(self._b)
                 for _, lookback in enumerate(F)])
        upper_bound_feature_building_lookback = \
            pd.to_timedelta(''.join((str(30 * int(self.L.split('M')[0])), 'D'))) // pd.to_timedelta(self._b) \
                if 'M' in self._L else pd.to_timedelta(self._L) // pd.to_timedelta(self._b)
        if upper_bound_feature_building_lookback < max_feature_building_lookback:
            raise ValueError('Lookback window for model training is smaller than the furthest lookback window '
                             'in feature building.')
        else:
            self._F = F

    @L.setter
    def L(self, L: str):
        max_feature_building_lookback = \
            max([pd.to_timedelta(lookback) // pd.to_timedelta(self._b) if 'M' not in lookback else
                 pd.to_timedelta(''.join((str(30 * int(lookback.split('M')[0])), 'D'))) // pd.to_timedelta(self._b)
                 for _, lookback in enumerate(self._F)])
        upper_bound_feature_building_lookback = \
            pd.to_timedelta(''.join((str(30 * int(L.split('M')[0])), 'D'))) // pd.to_timedelta(self._b) \
                if 'M' in L else pd.to_timedelta(L) // pd.to_timedelta(self._b)
        if upper_bound_feature_building_lookback < max_feature_building_lookback:
            raise ValueError('Lookback window for model training is smaller than the furthest lookback window '
                             'in feature building.')
        else:
            self._L = L

    @Q.setter
    def Q(self, Q: str):
        self._Q = Q

    @model_type.setter
    def model_type(self, model_type: str):
        self._model_type = model_type

    @staticmethod
    def correlation(cutoff_low: float = .01, cutoff_high: float = .01, insert: bool=True) \
            -> typing.Union[None, pd.DataFrame]:
        correlation_dd = dict()
        reader_obj = Reader(file=os.path.abspath('../data_centre/tmp/aggregate2022'))
        returns = reader_obj.returns_read(raw=False, cutoff_low=cutoff_low, cutoff_high=cutoff_high)
        for L in ['1D', '1W', '1M']:
            window = pd.to_timedelta('30D') // pd.to_timedelta('5T') if L == '1M'\
                else max(4, pd.to_timedelta(L) // pd.to_timedelta('5T'))
            correlation = \
                returns.rolling(window=window).corr().dropna().droplevel(axis=0, level=1).mean(axis=1)
            correlation = correlation.groupby(by=correlation.index).mean()
            correlation = correlation.resample('1D').mean()
            correlation.name = L
            correlation_dd[L] = correlation
        correlation = pd.DataFrame(correlation_dd)
        correlation = pd.melt(frame=correlation, value_name='value', var_name='lookback_window', ignore_index=False)
        if insert:
            print(f'[Insertion]: Correlation table...............................')
            correlation.to_sql(name='correlation', con=ModelBuilder.db_connect_correlation, if_exists='replace')
            print(f'[Insertion]: Correlation table is not completed.')
            return
        else:
            return correlation

    @staticmethod
    def covariance(cutoff_low: float = .01, cutoff_high: float = .01, insert: bool = True) \
            -> typing.Union[None, pd.DataFrame]:
        covariance_dd = dict()
        reader_obj = Reader(file=os.path.abspath('../data_centre/tmp/aggregate2022'))
        returns = reader_obj.returns_read(raw=False, cutoff_low=cutoff_low, cutoff_high=cutoff_high)
        for L in ['1D', '1W', '1M']:
            window = pd.to_timedelta('30D') // pd.to_timedelta('5T') if L == '1M' \
                else max(4, pd.to_timedelta(L) // pd.to_timedelta('5T'))
            covariance = \
                returns.rolling(window=window).cov().dropna().droplevel(axis=0, level=1).mean(axis=1)
            covariance = covariance.groupby(by=covariance.index).mean()
            covariance = covariance.resample('1D').mean()
            covariance.name = L
            covariance_dd[L] = covariance
        covariance = pd.DataFrame(covariance_dd)
        covariance = pd.melt(frame=covariance, value_name='value', var_name='lookback_window', ignore_index=False)
        if insert:
            print(f'[Insertion]: Covariance table...............................')
            covariance.to_sql(name='covariance', con=ModelBuilder.db_connect_correlation, if_exists='replace')
            print(f'[Insertion]: Covariance table is not completed.')
            return
        else:
            return covariance

    def getting_data(self, symbol: typing.Union[typing.Tuple[str], str, None], cross: bool, transformation: str = None,
                     **kwargs) -> pd.DataFrame:
        if isinstance(symbol, str):
            symbol = (symbol, symbol)
        if self._model_type != 'har_universal':
            feature_obj = ModelBuilder._factory_model_type_dd[self._model_type]
        else:
            feature_obj = ModelBuilder._factory_model_type_dd[self._model_type][cross]
        if self._model_type in ['har', 'har_dummy_markets', 'ar']:
            data = feature_obj.builder(symbol=symbol, F=self._F, df=kwargs['df'])
        elif self._model_type in ['har_csr', 'har_cdr']:
            data = feature_obj.builder(symbol=symbol, df=kwargs['df'], F=self._F, df2=kwargs['df2'])
        elif self._model_type in ['har_universal']:
            data = feature_obj.builder(df=kwargs['df'], F=self._F)
        elif self._model_type in ['risk_metrics']:
            data = feature_obj.builder(symbol=symbol, df=kwargs['df'], F=self._F)
        data = \
            pd.DataFrame(
                data=np.vectorize(
                    ModelBuilder._factory_transformation_dd[transformation]['transformation'])
                (data.values), index=data.index, columns=data.columns)
        if not data.filter(regex='_1W').empty:
            data = \
                rv_1w_correction(data, self._L) if data.filter(regex='_1W').iloc[:, 0].unique().shape[0] > 1 else data
        return data

    def clean_exog(self, data: pd.DataFrame, symbol: typing.Union[typing.Tuple[str], str]) -> pd.DataFrame:
        if self._model_type != 'har_universal':
            exog_rv = data.filter(regex=f'{symbol}')
            exog_rest = data.loc[:, ~data.columns.isin(exog_rv.columns.tolist())]
            exog_rv.replace(0, np.nan, inplace=True)
            exog_rv.ffill(inplace=True)
            exog = pd.concat([exog_rv, exog_rest], axis=1)
        return exog

    def rolling_metrics(self, cross: bool, symbol: typing.Union[typing.Tuple[str], str],
                        regression_type: str = 'linear',
                        transformation: str = None, **kwargs) -> typing.Tuple[pd.Series]:
        if (not cross) & (regression_type == 'ensemble'):
            raise TypeError('Ensemble and not cross are not compatible. Change regression type.')
        if self._model_type not in ModelBuilder.models:
            raise ValueError('Model type not available')
        else:
            y_series_test_name_dd = {True: 'RV', False: symbol}
            exog = \
                ModelBuilder.models_networks_dd[self._model_type][symbol] if (self._model_type != 'har_universal') \
                & cross else self.getting_data(symbol=symbol, transformation=transformation, cross=cross, **kwargs)
            if cross & (self._model_type != 'har_universal'):
                endog = ModelBuilder.reader_obj.rv_read(symbol=symbol)
            else:
                exog = self.clean_exog(exog, symbol) if self._model_type != 'har_universal' else exog
                endog = exog.pop(symbol) if self._model_type != 'har_universal' else exog.pop('RV')
            endog.replace(0, np.nan, inplace=True)
            endog.ffill(inplace=True)
            # if (transformation == 'log') & ((exog < 0).sum().sum() > 0):
            #     """
            #         Vertical shift
            #     """
            #     original_min = \
            #         abs(exog.min().min())
            #     negative_value_in_exog = (exog < 0).sum().sum() > 0
            #     exog = exog + original_min
            #     endog = endog + original_min
            #     # Delete if weird results
            #     exog = exog[exog[exog < 0].sum(axis=1) == 0]
            #     endog = endog.loc[exog.index]
            #     exog = \
            #         pd.DataFrame(
            #         data=np.vectorize(
            #         ModelBuilder._factory_transformation_dd[transformation]['transformation'])(exog.values),
            #             index=exog.index, columns=exog.columns)
            #     pdb.set_trace()
            #     exog = exog - original_min if ((transformation == 'log') & negative_value_in_exog & (not cross)) else \
            #         exog
            #     endog = pd.DataFrame(data=np.vectorize(
            #         ModelBuilder._factory_transformation_dd[transformation]['transformation'])
            #     (endog.values), index=endog.index, columns=[endog.name])
            #     endog = endog - original_min if ((transformation == 'log') & negative_value_in_exog & (not cross)) \
            #         else endog
        feature_obj = ModelBuilder._factory_model_type_dd[self._model_type] if self._model_type != 'har_universal' \
            else ModelBuilder._factory_model_type_dd[self._model_type][cross]
        regression = ModelBuilder._factory_regression_dd[regression_type]
        idx_ls = set(exog.index) if self._model_type != 'har_universal' else set(exog.index.get_level_values(0))
        idx_ls = list(idx_ls)
        idx_ls.sort()
        columns_name = \
            ['const'] + ['_'.join(('RV', F)) for _, F in enumerate(self._F)] if \
                ((self._model_type == 'har_universal') & (not cross)) else ['const'] + exog.columns.tolist()
        if (self._model_type == 'risk_metrics') & (not cross):
            columns_name.remove('const')
        coefficient = pd.DataFrame(data=np.nan, index=idx_ls, columns=columns_name)
        # tstats = pd.DataFrame(data=np.nan, index=idx_ls, columns=columns_name)
        # pvalues = pd.DataFrame(data=np.nan, index=idx_ls, columns=columns_name)
        coefficient_update = \
            list(exog.resample('1D').groups.keys()) if self._model_type != 'har_universal' else \
                np.unique(exog.index.get_level_values(0).date).tolist()
        coefficient_update.sort()
        coefficient_update = \
            [pd.to_datetime(date.strftime('%Y-%m-%d'), utc=True) for _, date in enumerate(coefficient_update)]
        y = list()
        left_date = max((pd.to_timedelta(self._L)//pd.to_timedelta('1D')), 1) if self._L != '1M' else \
            (pd.to_timedelta('30D')//pd.to_timedelta('1D'))
        for date in coefficient_update[left_date:-1]:
            start = \
                pd.to_datetime(date) - ModelBuilder.start_dd['1D'] if ModelBuilder.L_shift_dd[self._L] < 288 \
                else pd.to_datetime(date) - ModelBuilder.start_dd[self._L]
            if self._model_type != 'har_universal':
                X_train, y_train = exog.loc[(exog.index >= start) & (exog.index < pd.to_datetime(date, utc=True))],\
                endog.loc[(endog.index >= start) & (endog.index < pd.to_datetime(date, utc=True))]
            else:
                X_train, y_train = exog.loc[(exog.index.get_level_values(0) >= start) &
                                        (exog.index.get_level_values(0) < pd.to_datetime(date, utc=True))],\
                endog.loc[(endog.index.get_level_values(0) >= start) &
                          (endog.index.get_level_values(0) < pd.to_datetime(date, utc=True))]
            X_train.dropna(inplace=True)
            y_train.dropna(inplace=True)
            X_train.replace(np.inf, np.nan, inplace=True)
            X_train.replace(-np.inf, np.nan, inplace=True)
            y_train.replace(np.inf, np.nan, inplace=True)
            y_train.replace(-np.inf, np.nan, inplace=True)
            X_train.ffill(inplace=True)
            y_train.ffill(inplace=True)
            if transformation == 'log':
                """To be checked"""
                y_train.drop(X_train.loc[X_train.isnull().sum(axis=1) > 0].index, inplace=True, axis=0)
                X_train.drop(X_train.loc[X_train.isnull().sum(axis=1) > 0].index, inplace=True, axis=0)
            if self._model_type != 'har_universal':
                y_train.where(
                    ((y_train <= y_train.quantile(.75) + 1.5 * (y_train.quantile(.75) - y_train.quantile(.25))) &
                     (y_train >= y_train.quantile(.25) - 1.5 * (y_train.quantile(.75) - y_train.quantile(.25)))),
                    inplace=True)
            else:
                y_train = \
                    y_train.groupby(by=pd.Grouper(level=1), group_keys=True).apply(
                        lambda x: x.where((x <= x.quantile(.75) + 1.5 * (x.quantile(.75) - x.quantile(.25))) &
                                          (x >= x.quantile(.25) - 1.5 * (x.quantile(.75) - x.quantile(.25)))))
                y_train = y_train.droplevel(axis=0, level=0)
            old_N = X_train.shape[0]
            new_N = y_train.shape[0]
            y_train = y_train if isinstance(y_train, pd.Series) else y_train.iloc[:, 0]
            if y_train.any():
                intersection_date = list(set(X_train.index).intersection(set(y_train[y_train.isnull()].index)))
                X_train.drop(intersection_date, inplace=True, axis=0)
                y_train.dropna(inplace=True)
                new_N = y_train.shape[0]
            y_train = y_train.loc[X_train.index]
            if not cross:
                if self._model_type == 'har':
                    ModelBuilder.outliers_dd[self._L].append(new_N / old_N)
                if (not X_train.filter(regex='_1W').empty) & (self._model_type != 'har_universal'):
                    if X_train.filter(regex='_1W').iloc[:, 0].unique().shape[0] > 1:
                        X_train = rv_1w_correction(X_train, self._L)
                elif (not X_train.filter(regex='_1W').empty) & (self._model_type == 'har_universal'):
                    X_train = X_train.groupby(by=pd.Grouper(level=-1), group_keys=True).apply(rv_1w_correction)
                    X_train = X_train.droplevel(axis=0, level=0)
            else:
                if self._model_type != 'har_universal':
                    ModelBuilder.outliers_dd[self._L].append(new_N / old_N)
            if cross & (regression_type != 'linear') & (self._model_type != 'har_universal'):
                """
                    Retrain XGBoost in the first iteration or once per month (as time consuming to train everyday)
                    only for BTCUSDT. For other tokens, use model trained for BTCUSDT. 
                """
                if symbol == 'BTCUSDT':
                    if (not ModelBuilder._ensemble_model_store_dd[self._model_type]) | (date.date().day == 1):
                        rres = regression.fit(X_train, y_train)
                        ModelBuilder._ensemble_model_store_dd[self._model_type][date] = rres
                    else:
                        rres = ModelBuilder._ensemble_model_store_dd[self._model_type][date-relativedelta(days=1)]
                        ModelBuilder._ensemble_model_store_dd[self._model_type][date] = rres
                else:
                    while not ModelBuilder._ensemble_model_store_dd[self._model_type].get(date):
                        print(f'[Model Training]: Waiting for model to be trained on BTCUSDT on '
                              f'{date.strftime("%Y-%-m-%d")} - {symbol}.')
                        time.sleep(.1)
                    print(f'[Model Status]: Model trained on BTCUSDT on '
                          f'{date.strftime("%Y-%-m-%d")} is available. Transfer can be done - {symbol}.')
                    rres = ModelBuilder._ensemble_model_store_dd[self._model_type][date]
            else:
                if (self._model_type == 'risk_metrics') & (not cross):
                    pass
                else:
                    rres = regression.fit(X_train, y_train)
            if cross & (self._model_type != 'har_universal') & (regression_type != 'linear'):
                pass
            else:
                coefficient.loc[date, :] = \
                    np.array([feature_obj.factor, (1-feature_obj.factor)]) \
                        if ((self._model_type == 'risk_metrics') & (not cross)) \
                        else np.concatenate((np.array([rres.intercept_]), rres.coef_))
            """Test set"""
            test_date = (date + ModelBuilder.start_dd['1D']).date()
            X_test, y_test = exog.loc[test_date.strftime('%Y-%m-%d'), :], endog.loc[test_date.strftime('%Y-%m-%d')]
            y_test = y_test if isinstance(y_test, pd.Series) else y_test.iloc[:, 0]
            if (self._model_type == 'risk_metrics') & (not cross):
                pass
            else:
                X_test = X_test.assign(const=1) if regression_type == 'linear' else X_test
            X_test.replace(np.inf, np.nan, inplace=True)
            X_test.replace(-np.inf, np.nan, inplace=True)
            y_test.replace(np.inf, np.nan, inplace=True)
            y_test.replace(-np.inf, np.nan, inplace=True)
            X_test.ffill(inplace=True)
            y_test.ffill(inplace=True)
            if (self._model_type == 'risk_metrics') & (not cross):
                y_hat = \
                X_test.iloc[:, 0]*coefficient.loc[date, :].iloc[0]+X_test.iloc[:, 1]*coefficient.loc[date, :].iloc[1]
            else:
                y_hat = \
                    rres.predict(X_test.drop('const', axis=1)) if regression_type == 'linear' else rres.predict(X_test)
            y_hat = \
                pd.Series(data=ModelBuilder._factory_transformation_dd[transformation]['inverse'](y_hat),
                          index=y_test.index)
            y_test = \
                pd.Series(data=ModelBuilder._factory_transformation_dd[transformation]['inverse'](y_test.values),
                          index=y_test.index, name=y_series_test_name_dd[self._model_type == 'har_universal'])
            y.append(pd.concat([y_test, y_hat], axis=1))
            # """Tstats"""
            # tstats.loc[date, :] = \
            #     coefficient.loc[date, :].div((np.diag(np.matmul(X_train.values.transpose(),
            #                                                     X_train.values))/np.sqrt(X_train.shape[0])))
            # """Pvalues"""
            # pvalues.loc[date, :] = \
            #     2*(1-t.cdf(tstats.loc[date, :].values, df=X_train.shape[0]-coefficient.shape[1]-1))
        y = pd.concat(y).resample(self._s).sum() if self._model_type != 'har_universal' \
            else pd.concat(y).groupby(by=[pd.Grouper(level=-1), pd.Grouper(level=0, freq=self._s)]).sum()
        y = y.swaplevel(i='timestamp', j='symbol') if self._model_type == 'har_universal' else y
        tmp = y.groupby(by=pd.Grouper(level=0, freq=kwargs['agg'])) if self._model_type != 'har_universal' else \
        y.groupby(by=[pd.Grouper(level=-1), pd.Grouper(level=0, freq=kwargs['agg'])])
        mse = tmp.apply(lambda x: mean_squared_error(x.iloc[:, 0], x.iloc[:, -1]))
        qlike = tmp.apply(qlike_score)
        r2 = tmp.apply(lambda x: r2_score(x.iloc[:, 0], x.iloc[:, -1]))
        if self._model_type == 'har_universal':
            mse = mse.swaplevel(i='timestamp', j='symbol')
            r2 = r2.swaplevel(i='timestamp', j='symbol')
            qlike = qlike.swaplevel(i='timestamp', j='symbol')
            mse = mse.groupby(by=[pd.Grouper(level=-1),
                                  pd.Grouper(level=0,
                                             freq=kwargs['agg'])]).mean().groupby(by=pd.Grouper(level=-1)).mean()
            r2 = r2.groupby(by=[pd.Grouper(level=-1),
                                pd.Grouper(level=0, freq=kwargs['agg'])]).mean().groupby(by=pd.Grouper(level=-1)).mean()
            qlike = qlike.groupby(by=[pd.Grouper(level=-1),
                                      pd.Grouper(level=0,
                                                 freq=kwargs['agg'])]).mean().groupby(by=pd.Grouper(level=-1)).mean()
        else:
            mse = mse.resample(kwargs['agg']).mean()
            qlike = qlike.resample(kwargs['agg']).mean()
            r2 = r2.resample(kwargs['agg']).mean()
        mse = pd.Series(mse, name=symbol)
        qlike = pd.Series(qlike, name=symbol)
        r2 = pd.Series(r2, name=symbol)
        coefficient.ffill(inplace=True)
        coefficient.dropna(inplace=True)
        # tstats.ffill(inplace=True)
        # tstats.dropna(inplace=True)
        # pvalues.ffill(inplace=True)
        # pvalues.dropna(inplace=True)
        coefficient = coefficient.loc[coefficient.index[::pd.to_timedelta('1D')//pd.to_timedelta(self._b)], :]
        coefficient.columns = \
            coefficient.columns.str.replace('_'.join((symbol, '')), '') \
                if self._model_type != 'har_universal' else coefficient.columns.str.replace('_'.join(('RV', '')), '')
        # tstats.columns = tstats.columns.str.replace('_'.join((symbol, '')), '') \
        #     if self._model_type != 'har_universal' else tstats.columns.str.replace('_'.join(('RV', '')), '')
        # pvalues.columns = pvalues.columns.str.replace('_'.join((symbol, '')), '') \
        #     if self._model_type != 'har_universal' else pvalues.columns.str.replace('_'.join(('RV', '')), '')
        if self._model_type != 'har_universal':
            y = y.assign(model=self._model_type, symbol=symbol)
        else:
            y = y.reset_index()
            y['symbol'] = feature_obj.label_encoder_obj.inverse_transform(y['symbol'])
            y = y.assign(model=self._model_type)
            y = y.set_index('timestamp')
        y = \
            y.rename(columns={symbol: 'y', 0: 'y_hat'}) if self._model_type != 'har_universal'\
                else y.rename(columns={'RV': 'y', 0: 'y_hat'})
        ModelBuilder.models_forecast_dd[self._model_type].append(y)
        return mse, qlike, r2, coefficient#, tstats, pvalues

    def fill_model_network(self, symbol: str, transformation: str = None,
                           variance_explained: float = .9, **kwargs) -> None:
        """
            Method needed to fill in models_networks_dd for each model and each symbol.
            Steps for each token pair i,j in the universe:
                - regress i on features of j -> y_hat
                - store that y_hat with token j name in models_networks_dd
        """
        if self._model_type not in ModelBuilder.models:
            raise ValueError('Model type not available')
        else:
            rv = ModelBuilder.reader_obj.rv_read()
            rv = \
                pd.DataFrame(
                    data=np.vectorize(
                        ModelBuilder._factory_transformation_dd[transformation]['transformation'])
                    (rv.values), index=rv.index, columns=rv.columns)
            feature_obj = ModelBuilder._factory_model_type_dd[self._model_type]
        auxiliary_regression = ModelBuilder._factory_regression_dd['linear']
        if self._model_type != 'har_universal':
            if self._model_type in ['har', 'har_dummy_markets']:
                exog = pd.concat([feature_obj.builder(df=rv, symbol=symbol, F=self._F) for symbol in rv.columns],
                                 axis=1)
            elif self._model_type in ['har_cdr', 'har_csr']:
                exog = pd.concat([feature_obj.builder(df=rv, symbol=symbol, df2=kwargs['df2'],
                                                      F=self._F) for symbol in rv.columns], axis=1)
            filter_dd = {'har': lambda x: f'{x}_', 'har_dummy_markets': lambda x: f'{x}_|session',
                         'har_cdr': lambda x: f'{x}_|{x}_CDR', 'har_csr': lambda x: f'{x}_|{x}_CSR_'}
            try:
                endog = exog.pop(symbol)
            except UnboundLocalError:
                pdb.set_trace()
            auxiliary_tmp_dd = dict()
            for coin in ModelBuilder.coins:
                expression = filter_dd[self._model_type](coin)
                auxiliary_X_train = exog.filter(regex=expression)
                auxiliary_X_train = auxiliary_X_train.loc[:, ~auxiliary_X_train.columns.duplicated('first')]
                auxiliary_tmp_dd[coin] = \
                    auxiliary_regression.fit(auxiliary_X_train, endog).predict(auxiliary_X_train)
            exog = pd.DataFrame(auxiliary_tmp_dd, index=endog.index)
        exog_pca = ModelBuilder._pca_obj.fit_transform(exog)
        variance_ratio_percentage = np.cumsum(ModelBuilder._pca_obj.explained_variance_ratio_)
        variance_ratio_percentage = variance_ratio_percentage > variance_explained
        n_components = np.where(variance_ratio_percentage == True)[0][0]+1
        ModelBuilder.pca_components_models_dd[self._model_type][symbol].append(n_components)
        exog = pd.DataFrame(index=exog.index, columns=['_'.join(('PC', str(n+1))) for n in range(0, n_components)],
                            data=exog_pca[:, :n_components])
        ModelBuilder.models_networks_dd[self._model_type][symbol] = exog


    @staticmethod
    def reinitialise_models_forecast_dd():
        ModelBuilder.models_forecast_dd = {model: list() for _, model in enumerate(ModelBuilder.models) if model}

    def add_metrics(self, regression_type: str = 'linear', transformation: str = None,
                    cross: bool = False, **kwargs) -> None:
        if cross:
            if self._model_type != 'har_universal':
                for symbol in ModelBuilder.coins:
                    if self._model_type in ['har', 'har_dummy_markets', 'ar']:
                        self.fill_model_network(symbol=symbol, transformation=transformation,
                                                variance_explained=kwargs['variance_explained'])
                    elif self._model_type in ['har_cdr', 'har_csr']:
                        self.fill_model_network(symbol=symbol, transformation=transformation,
                                                variance_explained=kwargs['variance_explained'], df2=kwargs['df2'])
        if self._model_type != 'har_universal':
            coin_ls = set(self.coins).intersection(set(kwargs['df'].columns))
            coin_ls = list(coin_ls)
            coin_ls = ['COV'] if self._model_type == 'ar' else coin_ls
            r2_dd = dict([(pair, pd.DataFrame()) for _, pair in enumerate(coin_ls)])
            mse_dd = dict([(pair, pd.DataFrame()) for _, pair in enumerate(coin_ls)])
            qlike_dd = dict([(pair, pd.DataFrame()) for _, pair in enumerate(coin_ls)])
            coefficient_dd = dict([(pair, pd.DataFrame()) for _, pair in enumerate(coin_ls)])
            tstats_dd = dict([(pair, pd.DataFrame()) for _, pair in enumerate(coin_ls)])
            pvalues_dd = dict([(pair, pd.DataFrame()) for _, pair in enumerate(coin_ls)])
        else:
            r2_dd = dict()
            mse_dd = dict()
            qlike_dd = dict()
            coefficient_dd = dict()
            tstats_dd = dict()
            pvalues_dd = dict()

        def add_metrics_per_symbol(symbol: typing.Union[typing.Tuple[str], str],
                                   regression_type: str='linear', cross: bool=False,
                                   transformation=None, **kwargs) -> None:
            mse_s, qlike_s, r2_s, coefficient_df = \
                self.rolling_metrics(symbol=symbol, regression_type=regression_type,
                                     transformation=transformation, cross=cross, **kwargs) #, tstats_df, pvalues_df

            if isinstance(symbol, tuple):
                for series_s in [mse_s, qlike_s, r2_s]:
                    series_s.name = '_'.join(series_s.name)
                symbol = '_'.join(symbol)
            mse_dd[symbol] = mse_s
            qlike_dd[symbol] = qlike_s
            r2_dd[symbol] = r2_s
            coefficient_dd[symbol] = coefficient_df
            # tstats_dd[symbol] = tstats_df
            # pvalues_dd[symbol] = pvalues_df
        if self._model_type != 'har_universal':
            with concurrent.futures.ThreadPoolExecutor() as executor:
                add_metrics_per_symbol_results_dd = \
                    {symbol: executor.submit(add_metrics_per_symbol(regression_type=regression_type, **kwargs,
                                                                    transformation=transformation, symbol=symbol,
                                                                    cross=cross))
                     for symbol in ModelBuilder.coins}
        else:
            mse, qlike, r2, coefficient = \
                self.rolling_metrics(symbol=None, regression_type=regression_type, transformation=transformation,
                                     cross=cross, **kwargs) #, tstats, pvalues
            mse_dd['mse'] = mse
            qlike_dd['qlike'] = qlike
            r2_dd['r2'] = r2
            coefficient_dd['coefficient'] = coefficient
            # tstats_dd['tstats'] = tstats
            # pvalues_dd['pvalues'] = pvalues
        y = pd.concat(ModelBuilder.models_forecast_dd[self._model_type])
        y = y.assign(model=self._model_type)
        ModelBuilder.models_forecast_dd[self._model_type] = y
        ModelBuilder.remove_redundant_key(mse_dd)
        ModelBuilder.remove_redundant_key(qlike_dd)
        ModelBuilder.remove_redundant_key(r2_dd)
        ModelBuilder.remove_redundant_key(tstats_dd)
        ModelBuilder.remove_redundant_key(pvalues_dd)
        ModelBuilder.remove_redundant_key(coefficient_dd)
        r2 = pd.DataFrame(r2_dd).mean(axis=1)
        r2.name = 'values'
        r2 = pd.DataFrame(r2)
        mse = pd.DataFrame(mse_dd).mean(axis=1)
        mse.name = 'values'
        mse = pd.DataFrame(mse)
        qlike = pd.DataFrame(qlike_dd).mean(axis=1)
        qlike.name = 'values'
        qlike = pd.DataFrame(qlike)
        r2 = r2.assign(model=self._model_type)
        mse = mse.assign(model=self._model_type)
        qlike = qlike.assign(model=self._model_type)
        ModelBuilder.models_rolling_metrics_dd[self._model_type]['mse'] = mse
        ModelBuilder.models_rolling_metrics_dd[self._model_type]['qlike'] = qlike
        ModelBuilder.models_rolling_metrics_dd[self._model_type]['r2'] = r2
        if self._model_type == 'har_universal':
            #ModelBuilder.models_rolling_metrics_dd[self._model_type]['tstats'] = tstats
            #ModelBuilder.models_rolling_metrics_dd[self._model_type]['pvalues'] = pvalues
            ModelBuilder.models_rolling_metrics_dd[self._model_type]['coefficient'] = coefficient
        else:
            # ModelBuilder.models_rolling_metrics_dd[self._model_type]['tstats'] = \
            #     pd.DataFrame({symbol: tstats.mean() for symbol, tstats in tstats_dd.items()})
            # ModelBuilder.models_rolling_metrics_dd[self._model_type]['pvalues'] = \
            #     pd.DataFrame({symbol: pvalues.mean() for symbol, pvalues in pvalues_dd.items()})
            if not cross:
                ModelBuilder.models_rolling_metrics_dd[self._model_type]['coefficient'] = \
                    pd.DataFrame({symbol: coeff.mean() for symbol, coeff in coefficient_dd.items()})

    @staticmethod
    def remove_redundant_key(dd: dict) -> None:
        dd_copy = copy.copy(dd)
        for key, item in dd_copy.items():
            if isinstance(item, (pd.DataFrame, pd.Series)):
                if item.empty:
                    dd.pop(key)
            elif isinstance(item, list):
                if not item:
                    dd.pop(key)
