import glob
import os.path
import pdb
import typing
import torch
import pandas as pd
from data_centre.helpers import coin_ls
from plotly.subplots import make_subplots
import plotly.express as px
import plotly.graph_objs as go
from plotly.tools import mpl_to_plotly
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
import statsmodels.api as sm
from scipy.stats.mstats import winsorize
from statsmodels.regression.rolling import RollingOLS
import concurrent.futures
import itertools
from sklearn.cluster import KMeans
import pickle
import swifter
from sklearn.metrics import pairwise_distances
import seaborn as sns


class Plot:

    freq_ls = ['1D', '7D', '30D'] #'30T', '60T',

    def __init__(self, bucket: str = '5T'):
        self.returns = pd.read_csv(os.path.abspath(f'../data_centre/tmp/returns_global_{bucket}.csv'),
                                   index_col='Unnamed: 0',
                                   date_parser=lambda x: datetime.strptime(x, '%Y-%m-%d %H:%M:%S'))
        self.returns.replace(np.inf, np.nan, inplace=True)
        self.returns.replace(0, np.nan, inplace=True)
        self.returns.ffill(inplace=True)
        self.returns = self.returns.apply(lambda x: winsorize(x, (.005, .005)))
        self.returns.index.name = 'Time'
        # self.rv = pd.read_csv(os.path.abspath(f'../data_centre/tmp/rv_global_{bucket}.csv'),
        #                       index_col='Unnamed: 0',
        #                       date_parser=lambda x: datetime.strptime(x, '%Y-%m-%d %H:%M:%S'))
        # self.rv.replace(np.inf, np.nan, inplace=True)
        # self.rv.replace(0, np.nan, inplace=True)
        # self.rv.ffill(inplace=True)
        # self.rv = self.rv.apply(lambda x: winsorize(x, (.005, .005)))
        # self.rv.index.name = 'Time'
        # check = pd.melt(self.rv.reset_index(), var_name='symbol', value_name='value', id_vars='Time')
        # fig = px.box(check, y='value', color='symbol')
        # check2 = self.rv.copy()
        # check2 = pd.melt(check2.reset_index(), var_name='symbol', value_name='value', id_vars='Time')
        # fig2 = px.box(check2, y='value', color='symbol')

    def histogram(self) -> None:
        col_grid = 3
        row_grid = len(coin_ls)//col_grid
        sym_ls = [''.join((coin, 'usdt')).upper() for _, coin in enumerate(coin_ls)]
        fig = make_subplots(rows=row_grid, cols=col_grid, subplot_titles=sym_ls)
        for row in range(1, row_grid+1):
            for col in range(1, col_grid+1):
                fig.add_trace(go.Histogram(x=self.returns[f'{sym_ls[row+col-2]}'],
                                           name=f'{sym_ls[row+col-2]} - returns',
                                           marker_color='#4285F4', showlegend=True), row=row, col=col)
                fig.add_trace(go.Histogram(x=self.rv[f'{sym_ls[row + col - 2]}'],
                                           name=f'{sym_ls[row + col - 2]} - rv',
                                           marker_color='#34A853', opacity=0.75), row=row, col=col)
        fig.update_layout(title='Returns and Realised Volatility: Distributions (5min bucket).',
                          height=1500, width=1200, showlegend=False)
        if 'distribution_returns_rv.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./distribution_returns_rv.png'))
        else:
            fig.show()

    def daily_realised_vol(self) -> None:
        daily_realised_vol_df = pd.DataFrame()
        daily_realised_vol_df = \
            daily_realised_vol_df.assign(cross_average=self.rv.resample('D').sum().mean(axis=1),
                                         percentile05=self.rv.resample('D').sum().transpose().quantile(.05),
                                         percentile25=self.rv.resample('D').sum().transpose().quantile(.25),
                                         percentile75=self.rv.resample('D').sum().transpose().quantile(.75),
                                         percentile95=self.rv.resample('D').sum().transpose().quantile(.95),)
        daily_realised_vol_df = \
            pd.melt(daily_realised_vol_df.reset_index(), id_vars='Time', var_name='line', value_name='value')
        fig = px.line(data_frame=daily_realised_vol_df, x='Time', y='value', color='line')
        fig.update_layout(showlegend=None)

    def rv_per_symbol_line(self, freq='15T') -> None:
        rv_df = self.rv.resample(freq).sum()
        mean_per_sym_df = rv_df.groupby(by=[rv_df.index.hour, rv_df.index.minute]).mean()
        mean_per_sym_df.index = pd.date_range(start='00:00', end='23:45', freq=freq)
        mean_per_sym_df.index = [idx.strftime('%H:%M') for idx in mean_per_sym_df.index]
        mean_per_sym_df.ffill(inplace=True)
        hours_ls = list(mean_per_sym_df.index)
        mean_per_sym_df = pd.melt(mean_per_sym_df.reset_index(), var_name='symbol', value_name='value', id_vars='index')
        fig = px.line(mean_per_sym_df, x='index', y='value', color='symbol', labels={'index': '', 'value': ''})
        fig.add_vline(x=hours_ls.index('08:30'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='Europe open',
                      annotation_position='top right')
        fig.add_vline(x=hours_ls.index('00:00'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='Asia open',
                      annotation_position='top right')
        fig.add_vline(x=hours_ls.index('14:30'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='US open',
                      annotation_position='top right')
        fig.add_vline(x=hours_ls.index('06:00'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='Asia close',
                      annotation_position='bottom left')
        fig.add_vline(x=hours_ls.index('16:00'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='Europe close',
                      annotation_position='bottom right')
        fig.add_vline(x=hours_ls.index('21:00'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='US close',
                      annotation_position='bottom right')
        fig.update_xaxes(tickangle=45)
        fig.update_layout(height=500, width=800)
        if 'rv_per_symbol.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./rv_per_symbol.png'))
        else:
            fig.show()

    def diurnal_rv(self) -> None:
        rv_df = self.rv.resample('30T').sum()
        mean_group = rv_df.mean(axis=1).groupby(by=[rv_df.index.hour, rv_df.index.minute])
        diurnal_rv_df = pd.DataFrame()
        diurnal_rv_df = \
            diurnal_rv_df.assign(cross_average=mean_group.mean().values,
                                 percentile25=mean_group.quantile(.25).values,
                                 percentile75=mean_group.quantile(.75).values,
                                 percentile05=mean_group.quantile(.05).values,
                                 percentile95=mean_group.quantile(.95).values)
        diurnal_rv_df.index = pd.date_range(start='00:00', end='23:30', freq='30T')
        diurnal_rv_df.index = [idx.strftime('%H:%M') for idx in diurnal_rv_df.index]
        hours_ls = list(diurnal_rv_df.index)
        diurnal_rv_df.ffill(inplace=True)
        diurnal_rv_df = np.log(diurnal_rv_df)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=diurnal_rv_df.index, y=diurnal_rv_df.percentile95, fill=None, mode='lines',
                                 line_color='orange', line={'width': 1, 'dash': 'dash'}, showlegend=False))
        fig.add_trace(go.Scatter(x=diurnal_rv_df.index, y=diurnal_rv_df.percentile05, fill='tonexty', mode='lines',
                                 line_color='orange', line={'width': 1, 'dash': 'dash'}, showlegend=False))

        fig.add_trace(go.Scatter(x=diurnal_rv_df.index, y=diurnal_rv_df.percentile75, fill=None, mode='lines',
                                 line_color='green',  line={'width': 2, 'dash': 'dash'}, showlegend=False))

        fig.add_trace(go.Scatter(x=diurnal_rv_df.index, y=diurnal_rv_df.percentile25, fill='tonexty', mode='lines',
                                 line_color='green', line={'width': 2, 'dash': 'dash'}, showlegend=False))

        fig.add_trace(go.Scatter(x=diurnal_rv_df.index, y=diurnal_rv_df.cross_average, fill=None, mode='lines',
                                 line_color='blue', line={'width': 3}, showlegend=False))
        fig.add_vline(x=hours_ls.index('08:30'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='Europe open',
                      annotation_position='top right')
        fig.add_vline(x=hours_ls.index('16:00'), line_width=2,
                      line_dash='dash', line_color='red', annotation_text='Europe close',
                      annotation_position='bottom right')
        fig.add_vline(x=hours_ls.index('00:00'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='Asia open',
                      annotation_position='top right')
        fig.add_vline(x=hours_ls.index('06:00'), line_width=2,
                      line_dash='dash', line_color='red', annotation_text='Asia close',
                      annotation_position='bottom left')
        fig.add_vline(x=hours_ls.index('14:30'), line_width=2,
                      line_dash='dash', line_color='black', annotation_text='US open',
                      annotation_position='top right')
        fig.add_vline(x=hours_ls.index('21:00'), line_width=2,
                      line_dash='dash', line_color='red', annotation_text='US open',
                      annotation_position='bottom right')
        fig.update_xaxes(tickangle=45)
        fig.update_layout(height=500, width=800)
        if 'diurnal_rv.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./diurnal_rv.png'))
        fig.show()

    def daily_rv(self) -> None:
        daily_rv_df = pd.DataFrame()
        tmp_df = self.rv.resample('1D').sum()
        daily_rv_df = daily_rv_df.assign(cross_average=tmp_df.mean(axis=1),
                                         percentile05=tmp_df.transpose().quantile(.05),
                                         percentile95=tmp_df.transpose().quantile(.95),
                                         percentile25=tmp_df.transpose().quantile(.25),
                                         percentile75=tmp_df.transpose().quantile(.75))
        daily_rv_df = np.log(daily_rv_df)
        daily_rv_df.ffill(inplace=True)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=daily_rv_df.index, y=daily_rv_df.percentile95, fill=None, mode='lines',
                                 line_color='orange', line={'width': 1, 'dash': 'dash'}, showlegend=False))
        fig.add_trace(go.Scatter(x=daily_rv_df.index, y=daily_rv_df.percentile05, fill='tonexty', mode='lines',
                                 line_color='orange', line={'width': 1, 'dash': 'dash'}, showlegend=False))

        fig.add_trace(go.Scatter(x=daily_rv_df.index, y=daily_rv_df.percentile75, fill=None, mode='lines',
                                 line_color='green', line={'width': 2, 'dash': 'dash'}, showlegend=False))

        fig.add_trace(go.Scatter(x=daily_rv_df.index, y=daily_rv_df.percentile25, fill='tonexty', mode='lines',
                                 line_color='green', line={'width': 2, 'dash': 'dash'}, showlegend=False))

        fig.add_trace(go.Scatter(x=daily_rv_df.index, y=daily_rv_df.cross_average, fill=None, mode='lines',
                                 line_color='blue', line={'width': 3}, showlegend=False))
        fig.update_xaxes(tickangle=45)
        fig.update_layout(height=500, width=800)
        if 'daily_rv.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./daily_rv.png'))
        else:
            fig.show()

    def correlation_matrix(self) -> None:
        returns_df = self.returns.resample('5T').sum()
        corr_df = returns_df.corr()
        fig = go.Figure(data=go.Heatmap(z=corr_df.values, x=corr_df.columns, y=corr_df.columns, colorscale='Blues'))
        if 'corr_returns.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./corr_returns.png'))
        fig.show()

    def daily_mean_correlation_matrix(self) -> None:
        corr_df = self.returns.resample('1D').sum().expanding().corr().mean(level=1)
        fig = go.Figure(data=go.Heatmap(z=corr_df.values, x=corr_df.columns, y=corr_df.columns, colorscale='Blues'))
        if 'daily_mean_corr_returns.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./daily_mean_corr_returns.png'))
        fig.show()

    def daily_correlation(self) -> None:
        corr_df = self.returns.resample('1D').sum().expanding().corr()
        corr_df = \
            corr_df.loc[(corr_df.index.get_level_values(0),
                         corr_df.index.get_level_values(1).isin(['ETHUSDT', 'LTCUSDT', 'XLMUSDT',
                                                                 'ETCUSDT', 'XRPUSDT'])),
            'BTCUSDT'].reset_index().rename(columns={'BTCUSDT': 'value', 'level_1': 'symbol'}).set_index('Time')
        fig = px.line(corr_df, y='value', color='symbol', labels={'Time': '', 'value': ''})
        pdb.set_trace()
        if 'daily_correlation.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./daily_correlation.png'))
        fig.show()

    def daily_pairwise_correlation(self) -> None:
        corr_df = self.returns.resample('1D').sum().expanding(min_periods=3).corr().dropna()
        corr_ls = corr_df.values.flatten().tolist()
        corr_ls = [corr for _, corr in enumerate(corr_ls) if corr < 1]
        mean_corr = np.mean(corr_ls)
        fig = px.histogram(x=corr_ls, labels={'x': '', 'count': ''})
        fig.add_vline(x=mean_corr, line_color='orange')
        if 'daily_pairwise_correlation.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./daily_pairwise_correlation.png'))
        fig.show()

    @staticmethod
    def r2(bucket: str = '5T') -> None:
        r2_dd = dict([(''.join((pair, 'usdt')).upper(), None) for _, pair in enumerate(coin_ls)])
        # initialize lists
        list_1 = Plot.freq_ls
        list_2 = [''.join((sym, 'usdt')).upper() for _, sym in enumerate(coin_ls)]
        unique_combinations = list(list(zip(list_1, element))
                                   for element in itertools.product(list_2, repeat=len(list_1)))
        unique_combinations = list(set(list(itertools.chain(*unique_combinations))))

        def r2_per_symbol(freq: str, pair: str, bucket: str = '5T') -> None:
            rv_global_df = pd.read_csv(os.path.abspath(f'../data_centre/tmp/rv_global_{bucket}.csv'),
                                       index_col='Unnamed: 0')
            rv_global_df = rv_global_df.assign(rv_mkt=rv_global_df.mean(axis=1).values)
            endog = rv_global_df[f'{pair}']
            exog = sm.add_constant(rv_global_df['rv_mkt'])
            exog = exog.apply(lambda x: x.replace(np.inf, x.quantile(.995)))
            pdb.set_trace()
            rolling_ols = RollingOLS(endog, exog, window=pd.to_timedelta(freq) // pd.to_timedelta(bucket))
            rres = rolling_ols.fit(method='lstsq')
            tmp = pd.DataFrame(rres.rsquared, columns=['value'])
            tmp['freq'] = freq
            tmp['pair'] = pair
            if r2_dd.get(pair) is None:
                r2_dd[pair] = tmp
            else:
                r2_dd[pair] = pd.concat([r2_dd[pair], tmp], axis=0)
            print(f'[COMMONALITY PER BUCKET]: Monthly rolling R2 for {pair}, {freq} frequency is completed.')

        r2_per_symbol(freq='30D', pair='BTCUSDT')

        with concurrent.futures.ThreadPoolExecutor() as executor:
            r2_per_symbol_results = \
                {tmp[0]: executor.submit(r2_per_symbol, pair=tmp[1], bucket=bucket, freq=tmp[0])
                 for _, tmp in enumerate(unique_combinations)}

        r2_ls = list()
        for _, tmp in r2_dd.items():
            r2_ls.append(tmp)
        r2_df = pd.concat(r2_ls)
        r2_df.to_parquet(os.path.abspath('./tmp/r2'))

    @staticmethod
    def commonality() -> None:
        commonality_dd = dict([(freq, None) for _, freq in enumerate(Plot.freq_ls)])
        r2_df = pd.read_parquet(os.path.abspath('./tmp/r2'))
        r2_df.index = [datetime.strptime(idx, '%Y-%m-%d %H:%M:%S') for idx in r2_df.index]

        def commonality_pre_freq(freq: str) -> None:
            freq_df = r2_df.query(f'freq == "{freq}"')
            commonality = freq_df.groupby(by=freq_df.index.month).mean().ffill()
            commonality_dd[freq] = commonality.value.values

        with concurrent.futures.ThreadPoolExecutor() as executor:
            commonality_pre_freq_results = \
                {freq: executor.submit(commonality_pre_freq, freq=freq) for _, freq in enumerate(Plot.freq_ls)}
        commonality_df = pd.DataFrame(data=commonality_dd, index=pd.date_range('2022-01-01', '2022-12-31', freq='M'))
        commonality_df = pd.melt(commonality_df.reset_index(), id_vars='index', var_name='freq', value_name='value')
        fig = px.line(commonality_df, x=commonality_df['index'], y='value', color='freq',
                      labels={'index': '', 'value': ''})
        fig.update_xaxes(tickangle=45)
        fig.update_layout(showlegend=False)
        if 'commonality.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./commonality.png'))
        else:
            fig.show()

    @staticmethod
    def intraday_commonality() -> None:
        intraday_commonality_dd = dict([(freq, None) for _, freq in enumerate(Plot.freq_ls)])
        r2_df = pd.read_parquet(os.path.abspath('./tmp/r2'))
        r2_df.index = [datetime.strptime(idx, '%Y-%m-%d %H:%M:%S') for idx in r2_df.index]

        def intraday_commonality_pre_freq(freq: str) -> None:
            freq_df = r2_df.query(f'freq == "{freq}"')
            freq_df = pd.pivot_table(freq_df.reset_index(), columns='pair', values='value', index='index')
            freq_df = freq_df.resample('30T').mean()
            freq_df = pd.melt(freq_df.reset_index(), var_name='symbol', value_name='value', id_vars='index')
            freq_df = freq_df.set_index('index')
            commonality = freq_df.groupby(by=[freq_df.index.hour, freq_df.index.minute]).mean()
            intraday_commonality_dd[freq] = commonality.value.values

        with concurrent.futures.ThreadPoolExecutor() as executor:
            commonality_pre_freq_results = \
                {freq: executor.submit(intraday_commonality_pre_freq, freq=freq) for _, freq in enumerate(Plot.freq_ls)}

        idx_ls = pd.date_range(start='00:00', end='23:30', freq='30T')
        idx_ls = [idx.strftime('%H:%M') for idx in idx_ls]
        intraday_commonality_df = pd.DataFrame(intraday_commonality_dd, index=idx_ls)
        intraday_commonality_df = pd.melt(intraday_commonality_df.reset_index(),
                                          id_vars='index', value_name='value', var_name='freq')
        fig = px.line(intraday_commonality_df, x=intraday_commonality_df['index'],
                      y='value', color='freq', labels={'value': '', 'index': ''})
        fig.update_layout(showlegend=False)
        fig.update_xaxes(tickangle=45)
        if 'intraday_commonality.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./intraday_commonality.png'))
        fig.show()

    @staticmethod
    def metrics_plots():
        markers_ls = ['orange', 'green', 'blue']
        models_ls = list(set([name.split('/')[-1].split('_')[-1] for name in glob.glob('./tmp/*')]))
        models_ls.remove('dummy.pkl')
        markers_dd = {freq: markers_ls[i] for i, freq in enumerate(models_ls)}
        overall_metrics_df = pd.DataFrame()
        for _, model in enumerate(models_ls):
            r2_df = pd.read_parquet(os.path.abspath(f'./tmp/r2_model_{model}'))
            r2_df = r2_df.groupby(by=['freq', r2_df.index]).mean().reset_index().rename(columns={'level_1': 'index'})
            r2_df = r2_df.assign(metric='R2')
            r2_df = r2_df.assign(model=model)
            mse_df = pd.read_parquet(os.path.abspath(f'./tmp/mse_model_{model}'))
            mse_df = mse_df.groupby(by=['freq', mse_df.index]).mean().reset_index().rename(columns={'level_1': 'index'})
            mse_df = mse_df.assign(metric='MSE')
            mse_df = mse_df.assign(model=model)
            qlike_df = pd.read_parquet(os.path.abspath(f'./tmp/qlike_model_{model}'))
            qlike_df = \
                qlike_df.groupby(by=['freq', qlike_df.index]).mean().reset_index().rename(columns={'level_1': 'index'})
            qlike_df = qlike_df.assign(metric='QLIKE')
            qlike_df = qlike_df.assign(model=model)
            overall_metrics_df = pd.concat([overall_metrics_df, r2_df, mse_df, qlike_df])
        fig = px.line(overall_metrics_df,
                      x="index", y="value", color="model", facet_col="metric",
                      facet_row="freq", labels={'value': '', 'index': ''}, facet_col_spacing=0.05)
        fig.for_each_annotation(lambda name: name.update(text=name.text.replace('metric=', '')))
        fig.for_each_annotation(lambda name: name.update(text=name.text.replace('freq=', '')))
        fig.update_yaxes(matches=None, showticklabels=True)
        fig.update_xaxes(tickangle=45)
        fig.update_layout(height=1500, width=1200)
        if 'rolling_metrics_05.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath('./rolling_metrics_05.png'))
        fig.show()
        pdb.set_trace()

    @staticmethod
    def metrics_market_plots(market: str):
        markers_ls = ['orange', 'green', 'blue']
        markers_dd = {freq: markers_ls[i] for i, freq in enumerate(Plot.freq_ls)}
        r2_df = pd.read_parquet(os.path.abspath(f'./tmp/r2_model_{market}'))
        mse_df = pd.read_parquet(os.path.abspath(f'./tmp/mse_model_{market}'))
        qlike_df = pd.read_parquet(os.path.abspath(f'./tmp/qlike_model_{market}'))
        col_grid = 3
        row_grid = len(coin_ls)
        sym_ls = [''.join((coin, 'usdt')).upper() for _, coin in enumerate(coin_ls)]
        fig = make_subplots(rows=row_grid, cols=col_grid,
                            column_titles=['R2', 'MSE', 'QLIKE'], row_titles=sym_ls)
        for row in range(1, row_grid + 1):
            for _, freq in enumerate(Plot.freq_ls):
                tmp_df = r2_df.query(f'symbol == "{sym_ls[row - 1]}" and freq == "{freq}"').ffill()
                tmp2_df = mse_df.query(f'symbol == "{sym_ls[row - 1]}" and freq == "{freq}"').ffill()
                tmp3_df = qlike_df.query(f'symbol == "{sym_ls[row - 1]}" and freq == "{freq}"').ffill()
                fig.add_trace(go.Scatter(x=tmp_df.index, y=tmp_df.value, marker_color=markers_dd[freq],
                                         showlegend=False), row=row, col=1)
                fig.add_trace(go.Scatter(x=tmp2_df.index, y=tmp2_df.value, marker_color=markers_dd[freq],
                                         showlegend=False), row=row, col=2)
                fig.add_trace(go.Scatter(x=tmp3_df.index, y=tmp3_df.value, marker_color=markers_dd[freq],
                                         showlegend=False), row=row, col=3)
        fig.update_layout(height=1500, width=1200)
        if f'rolling_metrics_{market}.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(os.path.abspath(f'./rolling_metrics_{market}.png'))
        fig.show()

    @staticmethod
    def pvalues_plots_metrics(market: str) -> None:
        f = open(f'./tmp/pvalues_{market}.pkl', 'rb')
        pvalues_dd = pickle.load(f)
        pvalues_df = pd.DataFrame()
        f.close()
        for keys, values in pvalues_dd.items():
            tmp_df = values.copy()
            tmp_df = tmp_df.assign(freq=keys[0], symbol=keys[1])
            pvalues_df = pd.concat([pvalues_df, tmp_df])
        pvalues_df = \
            pvalues_df.loc[pvalues_df['symbol'].isin(['BTCUSDT', 'ETHUSDT', 'LTCUSDT']), :].set_index('index')
        event = pvalues_df.event.str.split('_')
        pvalues_df = pvalues_df.assign(zone=event.swifter.apply(lambda x: x[0]),
                                       moment=event.swifter.apply(lambda x: x[1]))
        pvalues_df.drop('event', axis=1, inplace=True)

        def pvalues_plots_metrics_per_freq(market: str, pvalues_df: pd.DataFrame, freq: str) -> None:
            pvalues_df = pvalues_df.query(f'freq == "{freq}"')
            freq_group = pvalues_df.groupby(by=['moment', 'zone', 'symbol'])
            weekly_pvalues_df = pd.DataFrame()
            for group in freq_group.groups.keys():
                tmp_df = freq_group.get_group(group)
                tmp_df = \
                    tmp_df.resample('W').agg({'value': 'mean', 'freq': 'first', 'symbol': 'first',
                                              'zone': 'first', 'moment': 'first'})
                weekly_pvalues_df = pd.concat([weekly_pvalues_df, tmp_df], axis=0)
            weekly_pvalues_df.sort_index(inplace=True)
            weekly_pvalues_df.ffill(inplace=True)
            fig = px.line(weekly_pvalues_df.reset_index(),
                          x="index", y="value", color="symbol", facet_col="zone",
                          facet_row="moment", labels={'value': 'p-value', 'index': ''}, title=f'P-value: {freq}')
            fig.add_hline(y=.05, line_dash='dash', line_color='black')
            fig.update_xaxes(tickangle=45)
            fig.for_each_annotation(lambda name: name.update(text=name.text.replace('zone=', '')))
            fig.for_each_annotation(lambda name: name.update(text=name.text.replace('moment=', '')))
            if f'pvalues_{market}_{freq}.png' not in os.listdir(os.path.abspath('./')):
                fig.write_image(os.path.abspath(f'./pvalues_{market}_{freq}.png'))
            fig.show()
            
        with concurrent.futures.ThreadPoolExecutor() as executor:
            metrics_plots_metrics_per_freq_results \
                = {freq: executor.submit(pvalues_plots_metrics_per_freq, market=market, pvalues_df=pvalues_df,
                   freq=freq) for _, freq in enumerate(Plot.freq_ls)}
        pdb.set_trace()

    @staticmethod
    def metrics_test_market_plots(log_scale: bool = True):
        log_extension_dd = {True: 'log', False: ''}
        markers_ls = ['orange', 'green', 'blue', 'purple']
        #markers_dd = {model: markers_ls[i] for i, model in enumerate(['Baseline', 'Dummy', 'CDR', 'CSR'])}
        markers_dd = {model: markers_ls[i] for i, model in enumerate(['Baseline'])}#, 'Dummy', 'CDR', 'CSR'])}
        #model_to_markers_dd = dict([('', 'Baseline'), ('dummy', 'Dummy'), ('cdr', 'CDR'), ('csr', 'CSR')])
        model_to_markers_dd = dict([('', 'Baseline')])#, ('dummy', 'Dummy'), ('cdr', 'CDR'), ('csr', 'CSR')])
        col_grid = 1
        row_grid = 3
        fig = make_subplots(rows=row_grid, cols=col_grid,
                            row_titles=['average R2', 'average MSE', 'average QLIKE'],
                            shared_xaxes=True)
        for model in model_to_markers_dd.keys():
            if model != '':
                if log_scale:
                    r2_df = \
                        pd.read_parquet(os.path.abspath
                                        (f'{"_".join(("./tmp/r2_model_test",model, log_extension_dd[log_scale]))}'))
                    mse_df = \
                        pd.read_parquet(os.path.abspath
                        (f'{"_".join(("./tmp/mse_model_test", model, log_extension_dd[log_scale]))}'))
                    qlike_df = \
                       pd.read_parquet(os.path.abspath
                                        (f'{"_".join(("./tmp/qlike_model_test", model, log_extension_dd[log_scale]))}'))
                else:
                    r2_df = \
                        pd.read_parquet(
                            os.path.abspath(f'{"_".join(("./tmp/r2_model_test", model))}'))
                    mse_df = \
                        pd.read_parquet(
                            os.path.abspath(f'{"_".join(("./tmp/mse_model_test", model))}'))
                    qlike_df = \
                        pd.read_parquet(
                            os.path.abspath(f'{"_".join(("./tmp/qlike_model_test", model))}'))
            else:
                if log_scale:
                    r2_df = pd.read_parquet(os.path.abspath(f'./tmp/r2_model_test_{log_extension_dd[log_scale]}'))
                    mse_df = pd.read_parquet(os.path.abspath(f'./tmp/mse_model_test_{log_extension_dd[log_scale]}'))
                    qlike_df = pd.read_parquet(os.path.abspath(f'./tmp/qlike_model_test_{log_extension_dd[log_scale]}'))
                else:
                    r2_df = pd.read_parquet(os.path.abspath(f'./tmp/r2_model_test'))
                    mse_df = pd.read_parquet(os.path.abspath(f'./tmp/mse_model_test'))
                    qlike_df = pd.read_parquet(os.path.abspath(f'./tmp/qlike_model_test'))
            # try:
            #     r2_df = r2_df.set_index('timestamp')
            # except Exception:
            #     pdb.set_trace()
            # r2_df = r2_df.groupby(by=r2_df.index).mean()
            r2_df = r2_df.mean(axis=1)#.set_index('timestamp')
            mse_df = mse_df.mean(axis=1)#.set_index('timestamp')
            # mse_df = mse_df.groupby(by=mse_df.index).mean()
            qlike_df = qlike_df.mean(axis=1)#.set_index('timestamp')
            # qlike_df = qlike_df.groupby(by=qlike_df.index).mean()
            tmp_df = r2_df
            tmp2_df = mse_df
            tmp3_df = qlike_df
            fig.add_trace(go.Scatter(x=tmp_df.index,
                                     y=tmp_df.values, marker_color=markers_dd[model_to_markers_dd[model]],
                                     showlegend=True, name=model_to_markers_dd[model],), row=1, col=1)
            fig.add_trace(go.Scatter(x=tmp2_df.index, y=tmp2_df.values,
                                     marker_color=markers_dd[model_to_markers_dd[model]], showlegend=False),
                          row=2, col=1)
            fig.add_trace(go.Scatter(x=tmp3_df.index, y=tmp3_df.values, showlegend=False,
                                     marker_color=markers_dd[model_to_markers_dd[model]]), row=3, col=1)
        fig.update_xaxes(tickangle=45, tickformat='%m-%Y')
        fig.update_layout(height=1500, width=1200)
        if log_scale:
            if f'rolling_metrics_test_{log_extension_dd[log_scale]}.png' not in os.listdir(os.path.abspath('./')):
                fig.write_image(os.path.abspath(f'./rolling_metrics_test_{log_extension_dd[log_scale]}.png'))
        else:
            if f'rolling_metrics_test.png' not in os.listdir(os.path.abspath('./')):
                fig.write_image(os.path.abspath(f'./rolling_metrics_test.png'))
        fig.show()

    @staticmethod
    def tensor_decomposition(log: bool = False) -> None:
        log_extension_dd = {True: '_log', False: ''}
        f = open(os.path.abspath(f'../plots/tmp/tensor{log_extension_dd[log]}.pkl'), 'rb')
        tensor = pickle.load(f)
        f.close()
        fig = \
            make_subplots(rows=3,
                          cols=7,
                          row_titles=['Day by Bucket', 'Symbol by Day', 'Symbol by Bucket'],
                          column_titles=['Top 3 Left', 'Eigenvalues', 'Top 3 Right',
                                         'Heatmap Left', 'Heatmap Right',
                                         'Barplot Left', 'Barplot Right'])
        for row, permutation in enumerate([(2, 0, 1), (1, 2, 0), (0, 2, 1)]):
            t = torch.from_numpy(tensor.data)
            t = t.permute(permutation)
            U, S, V = torch.svd(t, some=True, compute_uv=True, out=None)
            U, S, V = torch.mean(U, dim=0), torch.mean(S, dim=0), torch.mean(V, dim=0)
            similarity_U = 1 - pairwise_distances(U, metric='cosine')
            similarity_V = 1 - pairwise_distances(V, metric='cosine')
            fig.add_trace(go.Bar(y=U[:, 0], marker_line_width=0, marker_color='#008148', showlegend=False),
                          row=row + 1, col=1)
            fig.add_trace(go.Bar(y=U[:, 1], marker_line_width=0, marker_color='#C6C013', showlegend=False),
                          row=row + 1, col=1)
            fig.add_trace(go.Bar(y=U[:, 2], marker_line_width=0, marker_color='#EF8A17', showlegend=False),
                          row=row + 1, col=1)
            fig.add_trace(go.Bar(y=S[:30]/torch.sum(S[:30]),
                                 marker_line_width=0, marker_color='#0047AB', showlegend=False),
                          row=row + 1, col=2)
            fig.add_trace(go.Scatter(y=S[:30]/torch.sum(S[:30]), marker_line_width=0, marker_color='red',
                                     showlegend=False, mode='lines+markers', marker_size=6), row=row + 1, col=2)
            fig.add_trace(go.Bar(y=V[:, 0], marker_line_width=0, marker_color='#EF767A', showlegend=False),
                          row=row + 1, col=3)
            fig.add_trace(go.Bar(y=V[:, 1], marker_line_width=0, marker_color='#7D7ABC', showlegend=False),
                          row=row + 1, col=3)
            fig.add_trace(go.Bar(y=V[:, 2], marker_line_width=0, marker_color='#FFE347', showlegend=False),
                          row=row + 1, col=3)
            if row != 0:
                tmp = pd.DataFrame(similarity_U, columns=tensor.modes[-1].index, index=tensor.modes[-1].index)
                heatmap_U = sns.clustermap(tmp, metric='cosine', cmap='mako', yticklabels=True, xticklabels=True)
                # heatmap_U.dendrogram_row =\
                #     [tensor.modes[-1].index[i] for _, i in enumerate(heatmap_U.dendrogram_row.reordered_ind)]
                # heatmap_U.dendrogram_col =\
                #     [tensor.modes[-1].index[i] for _, i in enumerate(heatmap_U.dendrogram_col.reordered_ind)]
                plt.setp(heatmap_U.ax_heatmap.xaxis.get_majorticklabels(), fontsize=10)
                plt.setp(heatmap_U.ax_heatmap.yaxis.get_majorticklabels(), fontsize=10)
                plt.savefig(os.path.abspath(f'../plots/heatmap_clustered_row{row}'))
            bar_similarity_U = np.copy(similarity_U)
            bar_similarity_V = np.copy(similarity_V)
            bar_similarity_U[bar_similarity_U == 1] = np.nan
            bar_similarity_V[bar_similarity_V == 1] = np.nan
            bar_similarity_U = np.nanmean(bar_similarity_U, axis=0)
            bar_similarity_V = np.nanmean(bar_similarity_V, axis=0)
            if row == 1:
                pdb.set_trace()
                x = [tensor.modes[-1].index[i] for i, _ in enumerate(list(bar_similarity_U))]
            fig.add_trace(go.Bar(y=bar_similarity_U,
                                marker_line_width=0, marker_color='#0047AB', showlegend=False),
                         row=row + 1, col=4)
            fig.add_trace(go.Bar(y=bar_similarity_V,
                                marker_line_width=0, marker_color='#0047AB', showlegend=False),
                         row=row + 1, col=5)
            coloscale_U, coloscale_V = 'Viridis', 'Viridis'
            zmin_U, zmax_U = 0, 1
            if row == 0:
               coloscale_U = 'Greens'
               zmin_U, zmax_U = max(similarity_U.flatten()), min(similarity_U.flatten())
            coloscale_V = 'Reds'
            similarity_V = np.log(similarity_V)
            zmin_V, zmax_V = max(similarity_V.flatten()), min(similarity_V.flatten())
            fig.add_trace(go.Heatmap(z=similarity_U, hoverongaps=False, showscale=True,
                                    zmin=zmin_U, zmax=zmax_U, colorscale=coloscale_U), row=row + 1, col=6)
            fig.add_trace(go.Heatmap(z=similarity_V, hoverongaps=False, showscale=True,
                                    zmin=zmin_V, zmax=zmax_V, colorscale=coloscale_V), row=row + 1, col=7)
        fig.update_layout(height=1000, width=1200)
        if f'tensor_decomposition{log_extension_dd[log]}.png' not in os.listdir(os.path.abspath('./')):
            fig.write_image(f'./tensor_decomposition{log_extension_dd[log]}.png')
        fig.show()

    def all_plots(self):
        self.histogram()
        self.daily_realised_vol()
        self.rv_per_symbol_line()


if __name__ == '__main__':

    plot_obj = Plot()
    plot_obj.metrics_test_market_plots()
    #plot_obj.tensor_decomposition()
    #plot_obj.tensor_decomposition(log=True)
    pdb.set_trace()