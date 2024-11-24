# Standard libraries
import numpy as np
import pandas as pd
from typing import Tuple, Dict, Union, List
import itertools
import warnings

# Scikit-learn imports
from sklearn.preprocessing import StandardScaler, PolynomialFeatures
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel
from sklearn.metrics import r2_score, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit
from sklearn.exceptions import ConvergenceWarning

# Stats models
from statsmodels.regression.linear_model import OLS
from statsmodels.tools.tools import add_constant
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.arima.model import ARIMA
# Non-parametric regression
from statsmodels.nonparametric.smoothers_lowess import lowess

# SciPy
from scipy import signal
from scipy.stats import norm
from scipy.fft import fft, fftfreq, ifft


# Suppress specific warnings (optional)
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=ConvergenceWarning)

class PredictiveRegression:
    def _calculate_metrics(self, y_true, y_pred, n_params=0):
        """
        Calculates standardized error metrics for model evaluation.
        Inputs:
        y_true    : actual values
        y_pred    : predicted values
        n_params  : number of model parameters for AIC calculation
        
        Outputs:
        dict      : dictionary containing standard error metrics
            - r2   : R-squared value
            - rmse : Root Mean Square Error
            - mae  : Mean Absolute Error
            - aic  : Akaike Information Criterion
        """
        n = len(y_true)
        if n < 2:
            raise ValueError("Need at least 2 points to calculate metrics")
        
        # Calculate residuals
        residuals = y_true - y_pred
        
        # Calculate RSS and MSE
        rss = np.sum(residuals ** 2)
        mse = rss / n
        
        # R-squared
        tss = np.sum((y_true - np.mean(y_true)) ** 2)
        r2 = 1 - (rss / tss) if tss != 0 else 0
        
        # RMSE
        rmse = np.sqrt(mse)
        
        # MAE
        mae = np.mean(np.abs(residuals))
        
        # AIC (assuming Gaussian errors)
        # AIC = n * ln(RSS/n) + 2k, where k is number of parameters
        aic = n * np.log(rss/n) + 2 * n_params
        
        return {
            "r2": r2,
            "rmse": rmse,
            "mae": mae,
            "aic": aic
        }

    def _calculate_non_parametric_aic(self, y_true, y_pred, effective_params):
        """
        Calculates pseudo-AIC for non-parametric models.
        Inputs:
        y_true           : actual values
        y_pred           : predicted values
        effective_params : effective degrees of freedom for the model
        
        Outputs:
        float    : pseudo-AIC value
        """
        n = len(y_true)
        residuals = y_true - y_pred
        rss = np.sum(residuals ** 2)
        
        # Pseudo-AIC for non-parametric models
        # Uses trace of smoothing matrix as effective parameters
        return n * np.log(rss/n) + 2 * effective_params
    
    def clean_series(self, X, Y):
        """
        Aligns two time series based on shared valid data points.
        
        Inputs:
        X           : yearly time series data
        Y           : yearly time series data
        
        Outputs:
        tuple       : (X_aligned, Y_aligned)
        """
        if not isinstance(X, pd.Series):
            X = pd.Series(X)
        if not isinstance(Y, pd.Series):
            Y = pd.Series(Y)
        
        # Get overlapping indices where both series have valid data
        valid_indices = X.notna() & Y.notna()
        
        # Return aligned series with only valid data points
        return X[valid_indices], Y[valid_indices]

    def align_with_lag(self, X, Y, lag):
        """
        Aligns two time series based on a given lag value.
        
        Inputs:
        X    : yearly time series data
        Y    : yearly time series data
        lag  : integer lag value to shift X backwards
        
        Outputs:
        tuple: (X_aligned, Y_aligned) where X is shifted back by lag periods
        """
        if not isinstance(X, pd.Series):
            X = pd.Series(X)
        if not isinstance(Y, pd.Series):
            Y = pd.Series(Y)
        
        # Create pairs of indices for alignment
        Y_indices = Y[Y.notna()].index
        X_indices = X[X.notna()].index
        
        # Calculate lagged indices
        Y_start, Y_end = Y_indices.min(), Y_indices.max()
        X_start, X_end = X_indices.min() + lag, X_indices.max() + lag
        
        # Find overlapping period
        start_idx = max(Y_start, X_start)
        end_idx = min(Y_end, X_end)
        
        # If no overlap after lag, return empty series
        if start_idx > end_idx:
            return pd.Series(), pd.Series()
        
        # Align the series
        Y_aligned = Y.loc[start_idx:end_idx]
        X_aligned = X.loc[start_idx-lag:end_idx-lag]
        
        # Get only points where both series have valid data
        valid_indices = X_aligned.notna() & Y_aligned.notna()
        return X_aligned[valid_indices], Y_aligned[valid_indices]

    def max_lag(self, X, Y, max_lag_years=6):
        """
        Finds the lag that maximizes correlation between two time series.
        
        Inputs:
        X             : yearly time series data
        Y             : yearly time series data
        max_lag_years : maximum number of years to check for lag
        
        Outputs:
        tuple           : (optimal_lag, max_correlation, metrics)
        optimal_lag     : lag that maximizes correlation
        max_correlation : correlation value at optimal lag
        metrics        : evaluation metrics at optimal lag
        """
        if max_lag_years < 0:
            raise ValueError("max_lag_years must be non-negative")
        
        # Initial data cleaning to get valid data points
        X_clean, Y_clean = self.clean_series(X, Y)
        
        if len(X_clean) < 2 or len(Y_clean) < 2:
            raise ValueError("Insufficient valid data points in series")
        
        # Convert to numpy arrays for computation
        X_arr = np.array(X_clean)
        Y_arr = np.array(Y_clean)
        
        # Standardize series
        X_norm = (X_arr - np.mean(X_arr)) / (np.std(X_arr) + 1e-10)
        Y_norm = (Y_arr - np.mean(Y_arr)) / (np.std(Y_arr) + 1e-10)
        
        # Try different lags and compute correlations
        correlations = []
        valid_lags = []
        
        for lag in range(max_lag_years + 1):
            X_lagged, Y_aligned = self.align_with_lag(X_clean, Y_clean, lag)
            if len(X_lagged) >= 2:  # Need at least 2 points for correlation
                corr = np.corrcoef(X_lagged, Y_aligned)[0, 1]
                if not np.isnan(corr):
                    correlations.append(corr)
                    valid_lags.append(lag)
        
        if not correlations:
            # If no valid correlations found, return lag 0
            X_aligned, Y_aligned = self.clean_series(X, Y)
            return 0, 0, self._calculate_metrics(Y_aligned, X_aligned, n_params=1)
        
        # Find optimal lag
        best_idx = np.argmax(np.abs(correlations))
        optimal_lag = valid_lags[best_idx]
        max_correlation = correlations[best_idx]
        
        # Calculate metrics at optimal lag
        X_lagged, Y_aligned = self.align_with_lag(X, Y, optimal_lag)
        metrics = self._calculate_metrics(Y_aligned, X_lagged, n_params=1)
        
        return optimal_lag, max_correlation, metrics 

    def _do_cross_validation(self, X, Y, model_func, params, k_folds=5):
        """
        Helper function to perform cross validation for any model
        Inputs:
        X           : aligned X data for validation
        Y           : aligned Y data for validation
        model_func  : model fitting function
        params      : dictionary of model parameters
        k_folds     : number of folds for CV
        
        Outputs:
        tuple       : (cv_score, cv_error)
        cv_score    : mean R2 score across folds
        cv_error    : standard deviation of R2 scores across folds
        """
        # Initialize time series cross-validation
        tscv = TimeSeriesSplit(n_splits=k_folds, test_size=max(2, len(X) // (k_folds + 1)))
        val_scores = []
        
        try:
            for train_idx, val_idx in tscv.split(X):
                try:
                    # Split data into training and validation sets
                    X_train = X.iloc[train_idx]
                    Y_train = Y.iloc[train_idx]
                    X_val = X.iloc[val_idx]
                    Y_val = Y.iloc[val_idx]
                    
                    # Convert to numpy arrays if needed
                    if isinstance(X_train, pd.Series):
                        X_train = X_train.values
                    if isinstance(Y_train, pd.Series):
                        Y_train = Y_train.values
                    if isinstance(X_val, pd.Series):
                        X_val = X_val.values
                    if isinstance(Y_val, pd.Series):
                        Y_val = Y_val.values
                    
                    # Reshape arrays
                    X_train = X_train.reshape(-1, 1)
                    Y_train = Y_train.reshape(-1)
                    X_val = X_val.reshape(-1, 1)
                    Y_val = Y_val.reshape(-1)
                    
                    # Fit model
                    if 'X' in params and 'Y' in params:
                        model = model_func(**params)
                    else:
                        model = model_func(X=X_train, Y=Y_train)
                    
                    # Get predictions
                    if hasattr(model, 'predict'):
                        Y_pred = model.predict(X_val)
                    elif callable(model):
                        Y_pred = model(X_val)
                    else:
                        Y_pred = model.forecast(steps=len(val_idx), exog=X_val)
                    
                    # Handle array-like predictions
                    Y_pred = np.array(Y_pred).reshape(-1)
                    Y_val = np.array(Y_val).reshape(-1)
                    
                    # Calculate R2 score for this fold
                    fold_score = r2_score(Y_val, Y_pred)
                    if not np.isnan(fold_score):
                        val_scores.append(fold_score)
                        
                except Exception as e:
                    print(f"Warning: Error in fold: {str(e)}")
                    continue
            
            if not val_scores:
                return np.nan, np.nan
            
            # Calculate mean and standard deviation of scores
            cv_score = np.mean(val_scores)
            cv_error = np.std(val_scores)
            
            return cv_score, cv_error
        
        except Exception as e:
            print(f"Error in cross-validation: {str(e)}")
            return np.nan, np.nan
    
    def time_series_regression(self, X, Y, trend_method='linear', period=None, max_harmonics=3):
        """
        Decomposes time series into trend and cyclical components with prediction through 2024.
        X: pd.Series or array of years
        Y: pd.Series or array of values
        trend_method: str, regression method ('linear', 'polynomial', 'lowess', 'gaussian', 'arima')
        period: int or None, forced period for cyclical component
        max_harmonics: int, maximum number of harmonics for cyclical analysis
        """
        X = pd.Series(X) if not isinstance(X, pd.Series) else X.copy()
        Y = pd.Series(Y) if not isinstance(Y, pd.Series) else Y.copy()
        
        # Get clean data
        X_clean, Y_clean = self.clean_series(X, Y)
        n_clean = len(X_clean)
        
        # Create extended range maintaining original values
        if X_clean.max() < 2024:
            future_x = np.arange(X_clean.max() + 1, 2025)
            X_extended = np.concatenate([X_clean.values, future_x])
        else:
            X_extended = X_clean.values
        
        # Get trend components
        trend_results = self.trend_regression(X_extended, Y_clean.values, method=trend_method)
        trend = trend_results['plot_data']['Y_pred']
        
        # Detrend using only historical data
        detrended = Y_clean.values - trend[:n_clean]
        
        # Get cycle components for full range
        cycle_results = self.cyclicality_analysis(detrended, X_extended, period, max_harmonics)
        cycle = cycle_results['cycle']
        
        # Ensure trend and cycle have same length
        n_total = len(X_extended)
        trend = trend[:n_total]
        cycle = cycle[:n_total]
        
        # Combine predictions
        predictions = trend + cycle
        future_predictions = predictions[n_clean:]
        
        return {
            'trend': pd.Series(trend, index=X_extended),
            'cycle': pd.Series(cycle, index=X_extended),
            'clean_years': X_clean,
            'clean_data': Y_clean,
            'future_predictions': pd.Series(future_predictions, index=X_extended[n_clean:]),
            'trend_results': trend_results,
            'cycle_results': cycle_results,
            'plot_data': pd.DataFrame({
                'time': X_extended,
                'original': np.concatenate([Y_clean.values, np.full(len(X_extended) - n_clean, np.nan)]),
                'trend': trend,
                'cycle': cycle,
                'combined': predictions
            })
        }

    def trend_regression(self, X, Y, method='linear'):
        """
        Performs trend analysis through 2024 using specified regression method.
        X: array-like of years
        Y: array-like of values
        method: str, regression method ('linear', 'polynomial', 'lowess', 'gaussian', 'arima')
        """
        X = np.asarray(X)
        Y = np.asarray(Y)
        n_hist = len(Y)  # Length of historical data
        
        if method == 'linear':
            results = self.linear_regression(X[:n_hist], Y)
        elif method == 'polynomial':
            results = self.polynomial_regression(X[:n_hist], Y)
        elif method == 'lowess':
            results = self.lowess_regression(X[:n_hist], Y)
        elif method == 'gaussian':
            results = self.gaussian_process_regression(X[:n_hist], Y)
        elif method == 'arima':
            results = self.arima_regression(X[:n_hist], Y)
        else:
            raise ValueError(f"Unsupported trend method: {method}")
        
        # Get predictions for full range
        plot_data = results['plot_data']
        if X.max() > plot_data['X'].max():
            if method == 'linear':
                future_pred = results['model'].predict(add_constant(X[n_hist:]))
            elif method == 'polynomial':
                future_pred = results['model'].predict(results['poly'].transform(X[n_hist:].reshape(-1, 1)))
            elif method == 'arima':
                future_pred = results['model'].forecast(steps=len(X) - n_hist, exog=X[n_hist:].reshape(-1, 1))
            elif method in ['lowess', 'gaussian']:
                future_pred = np.interp(X[n_hist:], plot_data['X'], plot_data['Y_pred'])
            
            # Combine historical and future predictions
            plot_data = pd.DataFrame({
                'X': X,
                'Y_data': np.concatenate([Y, np.full(len(X) - n_hist, np.nan)]),
                'Y_pred': np.concatenate([plot_data['Y_pred'], future_pred])
            })
        
        return {
            'plot_data': plot_data,
            'prediction': results['prediction'],
            'metrics': {
                'r2': results['r2'],
                'rmse': results['rmse'],
                'mae': results['mae'],
                'aic': results['aic']
            },
            'model': results.get('model', None),
            'poly': results.get('poly', None)
        }

    def cyclicality_analysis(self, Y, X, period=None, max_harmonics=3):
        """
        Analyzes cyclical component with prediction through 2024.
        Y: array-like of detrended values
        X: array-like of years
        period: forced period length or None for automatic detection
        max_harmonics: maximum number of harmonics to consider
        """
        Y = np.asarray(Y)
        X = np.asarray(X)
        
        # Get FFT results from actual data
        Y_clean = Y[~np.isnan(Y)]
        fft_result = fft(Y_clean)
        T = len(Y_clean)
        freq = fftfreq(T, d=1)
        
        # Basic reconstruction for original period
        cycle_orig = np.real(ifft(fft_result))
        
        # Create extended cycle for full range
        n_total = len(X)
        if n_total > T:
            n_repeats = int(np.ceil((n_total - T) / T))
            extended_cycle = np.tile(cycle_orig, n_repeats + 1)[:n_total]
        else:
            extended_cycle = cycle_orig[:n_total]
        
        # Calculate metrics on original data
        residuals = Y_clean - cycle_orig
        mse = np.mean(residuals**2)
        rmse = np.sqrt(mse)
        r2 = 1 - np.sum(residuals**2) / np.sum((Y_clean - np.mean(Y_clean))**2)
        
        return {
            'cycle': extended_cycle,
            'prediction': extended_cycle[-1] if len(extended_cycle) > 0 else None,
            'period': None,
            'metrics': {
                'r2': r2,
                'rmse': rmse,
                'aic': T * np.log(mse) + 2
            }
        }

    def linear_regression(self, X, Y, do_cv=True, k_folds=5):
        """
        Performs linear regression with optional cross validation.
        """
        # Find optimal lag and align series
        best_lag, _, _ = self.max_lag(X, Y)
        X_aligned, Y_aligned = self.align_with_lag(X, Y, best_lag)
        
        # Fit model
        X_const = add_constant(X_aligned)
        model = OLS(Y_aligned, X_const).fit()
        
        # Make prediction
        current_X = X[2024 if 2024 in X.index else X.index.max()]
        # Use iloc to avoid the deprecation warning
        next_year_pred = model.params.iloc[0] + model.params.iloc[1] * current_X
        
        # Create plot data
        plot_data = pd.DataFrame({
            'X': X_aligned,
            'Y_data': Y_aligned,
            'Y_pred': model.fittedvalues
        })
        
        # Calculate standardized metrics
        metrics = self._calculate_metrics(Y_aligned, model.fittedvalues, n_params=2)
        
        results = {
            "lag": best_lag,
            "prediction": next_year_pred,
            "r2": metrics["r2"],
            "rmse": metrics["rmse"],
            "mae": metrics["mae"],
            "aic": metrics["aic"],
            "plot_data": plot_data
        }
        
        if do_cv:
            def ols_model(X, Y):
                # Ensure proper dimensions for training data
                X = X.reshape(-1)  # Flatten X
                X_const = add_constant(X)  # Add constant term
                model = OLS(Y, X_const).fit()
                
                # Return a prediction function that handles new data properly
                def predict(X_new):
                    X_new = X_new.reshape(-1)  # Flatten new X
                    X_new_const = add_constant(X_new)  # Add constant term
                    return model.predict(X_new_const)
                
                return predict
            
            cv_score, cv_error = self._do_cross_validation(
                X_aligned, 
                Y_aligned,
                ols_model,
                {},
                k_folds
            )
            results.update({
                'cv_score': cv_score,
                'cv_error': cv_error
            })
        
        return results

    def polynomial_regression(self, X, Y, degree=2, do_cv=True, k_folds=5):
        """
        Performs polynomial regression.
        Inputs:
        X       : yearly time series data 
        Y       : yearly time series data
        degree  : polynomial degree
        do_cv   : whether to perform cross validation
        k_folds : number of folds for CV
        
        Outputs:
        dict    : {
            'lag'        : optimal lag value,
            'prediction' : predicted value for next period,
            'r2'        : R-squared value,
            'rmse'      : root mean square error,
            'mae'       : mean absolute error,
            'aic'       : Akaike Information Criterion,
            'plot_data' : pandas DataFrame with X, Y_pred, Y_data columns,
            'cv_score'  : mean validation score (if do_cv=True),
            'cv_error'  : std of validation scores (if do_cv=True)
        }
        """
        # Find optimal lag and align series
        best_lag, _, _ = self.max_lag(X, Y)
        X_aligned, Y_aligned = self.align_with_lag(X, Y, best_lag)
        
        # Drop NaN values before fitting
        valid_mask = ~np.isnan(X_aligned) & ~np.isnan(Y_aligned)
        X_clean = X_aligned[valid_mask].values if hasattr(X_aligned, 'values') else X_aligned[valid_mask]
        Y_clean = Y_aligned[valid_mask].values if hasattr(Y_aligned, 'values') else Y_aligned[valid_mask]
        
        # Prepare polynomial features
        X_array = X_clean.reshape(-1, 1)
        poly = PolynomialFeatures(degree)
        X_poly = poly.fit_transform(X_array)
        
        # Fit model
        model = OLS(Y_clean, X_poly).fit()
        
        # Make prediction for next year
        current_X = X[2024 if 2024 in X.index else X.index.max()]
        if np.isnan(current_X):
            next_year_pred = np.nan
        else:
            next_year_pred = model.predict(poly.transform([[current_X]]))[0]
        
        # Get predictions for plotting
        X_plot = X_array
        Y_pred = model.predict(poly.transform(X_plot))
        
        # Create plot data
        plot_data = pd.DataFrame({
            'X': X_aligned[valid_mask],
            'Y_data': Y_aligned[valid_mask],
            'Y_pred': Y_pred
        })
        
        # Calculate metrics
        metrics = self._calculate_metrics(Y_clean, Y_pred, n_params=degree + 1)
        
        results = {
            "lag": best_lag,
            "prediction": next_year_pred,
            "r2": metrics["r2"],
            "rmse": metrics["rmse"],
            "mae": metrics["mae"],
            "aic": metrics["aic"],
            "plot_data": plot_data
        }
        
        if do_cv:
            def poly_model(X, Y):
                # Drop NaN values
                valid = ~np.isnan(X) & ~np.isnan(Y)
                X, Y = X[valid], Y[valid]
                
                X = X.reshape(-1, 1)
                poly_cv = PolynomialFeatures(degree)
                X_poly_cv = poly_cv.fit_transform(X)
                model_cv = OLS(Y, X_poly_cv).fit()
                
                def predict(X_new):
                    X_new = X_new.reshape(-1, 1)
                    nan_mask = np.isnan(X_new)
                    predictions = np.full(len(X_new), np.nan)
                    if not np.all(nan_mask):
                        predictions[~nan_mask] = model_cv.predict(
                            poly_cv.transform(X_new[~nan_mask])
                        )
                    return predictions
                
                return predict
            
            cv_score, cv_error = self._do_cross_validation(
                X_aligned,
                Y_aligned,
                poly_model,
                {},
                k_folds
            )
            results.update({
                'cv_score': cv_score,
                'cv_error': cv_error
            })
        
        return results
    
    def arima_regression(self, X, Y, max_p=3, max_d=2, max_q=3, do_cv=True, k_folds=5):
        """
        Performs ARIMA regression with simplified implementation.
        """
        # Find optimal lag and align series
        best_lag, _, _ = self.max_lag(X, Y)
        X_aligned, Y_aligned = self.align_with_lag(X, Y, best_lag)
        
        # Convert to numpy arrays if needed
        X_arr = X_aligned.values.reshape(-1, 1) if hasattr(X_aligned, 'values') else X_aligned.reshape(-1, 1)
        Y_arr = Y_aligned.values if hasattr(Y_aligned, 'values') else Y_aligned
        
        # Determine minimum differencing order
        d_min = 0 if adfuller(Y_arr)[1] < 0.05 else 1
        
        # Find best ARIMA model
        best_aic = np.inf
        best_model = None
        best_params = None
        
        # Grid search for parameters
        for p, d, q in itertools.product(
            range(max_p + 1),
            range(d_min, max_d + 1),
            range(max_q + 1)
        ):
            try:
                model = ARIMA(Y_arr, exog=X_arr, order=(p, d, q)).fit()
                if model.aic < best_aic:
                    best_aic = model.aic
                    best_model = model
                    best_params = (p, d, q)
            except:
                continue
                
        if best_model is None:
            raise ValueError("Could not fit any ARIMA model with given parameters")
        
        # Get fitted values
        Y_pred = best_model.fittedvalues
        
        # Make prediction for next period
        current_X = X_arr[-1]
        try:
            next_year_pred = best_model.forecast(steps=1, exog=np.array([[current_X]]))[0]
        except:
            next_year_pred = Y_pred[-1]  # Fallback to last fitted value
        
        # Create plot data
        plot_data = pd.DataFrame({
            'X': X_aligned,
            'Y_data': Y_aligned,
            'Y_pred': Y_pred
        })
        
        # Calculate metrics
        n_params = sum(best_params) + 1
        metrics = self._calculate_metrics(Y_aligned, Y_pred, n_params=n_params)
        
        results = {
            "lag": best_lag,
            "prediction": float(next_year_pred),
            "r2": metrics["r2"],
            "rmse": metrics["rmse"],
            "mae": metrics["mae"],
            "aic": metrics["aic"],
            "params": best_params,
            "plot_data": plot_data
        }
        
        if do_cv:
            def arima_model(X, Y):
                # Fit ARIMA model with best parameters
                X = X.reshape(-1, 1)
                model = ARIMA(Y, exog=X, order=best_params).fit()
                
                # Return simple prediction function
                return lambda x: model.forecast(steps=len(x), exog=x.reshape(-1, 1))
            
            cv_score, cv_error = self._do_cross_validation(
                X_aligned,
                Y_aligned,
                arima_model,
                {},
                k_folds
            )
            results.update({
                'cv_score': cv_score,
                'cv_error': cv_error
            })
        
        return results

    def lowess_regression(self, X, Y, frac=0.3, do_cv=True, k_folds=5):
        """
        Performs LOWESS regression with proper vector handling.
        """
        # Find optimal lag and align series
        best_lag, _, _ = self.max_lag(X, Y)
        X_aligned, Y_aligned = self.align_with_lag(X, Y, best_lag)
        
        # Convert to numpy arrays if needed
        X_arr = X_aligned.values if hasattr(X_aligned, 'values') else X_aligned
        Y_arr = Y_aligned.values if hasattr(Y_aligned, 'values') else Y_aligned
        
        # Fit LOWESS model
        smoothed = lowess(
            Y_arr,
            X_arr,
            frac=frac,
            return_sorted=True
        )
        
        # Make prediction
        if isinstance(X, pd.Series):
            current_X = X[2024 if 2024 in X.index else X.index.max()]
        else:
            current_X = X[-1]
        next_year_pred = np.interp(current_X, smoothed[:, 0], smoothed[:, 1])
        
        # Create plot data and interpolate fitted values
        X_sorted_idx = np.argsort(X_arr)
        Y_pred = np.interp(X_arr, smoothed[:, 0], smoothed[:, 1])
        
        plot_data = pd.DataFrame({
            'X': X_aligned,
            'Y_data': Y_aligned,
            'Y_pred': Y_pred
        })
        
        # Calculate effective parameters (degrees of freedom)
        n = len(X_arr)
        effective_params = max(1, int(frac * n))
        
        # Calculate metrics
        metrics = self._calculate_metrics(Y_aligned, Y_pred, n_params=effective_params)
        
        results = {
            "lag": best_lag,
            "prediction": float(next_year_pred),
            "r2": metrics["r2"],
            "rmse": metrics["rmse"],
            "mae": metrics["mae"],
            "aic": self._calculate_non_parametric_aic(Y_aligned, Y_pred, effective_params),
            "plot_data": plot_data
        }
        
        if do_cv:
            def lowess_model(X, Y):
                # Ensure X and Y are 1D arrays
                X = X.ravel()
                Y = Y.ravel()
                
                # Fit LOWESS on training data
                smoothed_train = lowess(Y, X, frac=frac, return_sorted=True)
                
                # Return prediction function that handles vectors properly
                def predict(X_new):
                    X_new = X_new.ravel()  # Ensure input is 1D
                    return np.interp(X_new, smoothed_train[:, 0], smoothed_train[:, 1])
                
                return predict
            
            cv_score, cv_error = self._do_cross_validation(
                X_aligned,
                Y_aligned,
                lowess_model,
                {},
                k_folds
            )
            results.update({
                'cv_score': cv_score,
                'cv_error': cv_error
            })
        
        return results

    def gaussian_process_regression(self, X, Y, length_scale=1.0, do_cv=True, k_folds=5):
        """
        Performs Gaussian Process Regression.
        Inputs:
        X            : yearly time series data
        Y            : yearly time series data
        length_scale : RBF kernel length scale parameter
        do_cv        : whether to perform cross validation
        k_folds      : number of folds for CV
        
        Outputs:
        dict    : {
            'lag'        : optimal lag value,
            'prediction' : predicted value for next period,
            'r2'        : R-squared value,
            'rmse'      : root mean square error,
            'mae'       : mean absolute error,
            'aic'       : AIC value,
            'std'       : prediction standard deviation,
            'plot_data' : pandas DataFrame with X, Y_pred, Y_data columns,
            'cv_score'  : mean validation score (if do_cv=True),
            'cv_error'  : std of validation scores (if do_cv=True)
        }
        """
        # Find optimal lag and align series
        best_lag, _, _ = self.max_lag(X, Y)
        X_aligned, Y_aligned = self.align_with_lag(X, Y, best_lag)
        
        # Drop NaN values before fitting
        valid_mask = ~np.isnan(X_aligned) & ~np.isnan(Y_aligned)
        X_clean = X_aligned[valid_mask].values if hasattr(X_aligned, 'values') else X_aligned[valid_mask]
        Y_clean = Y_aligned[valid_mask].values if hasattr(Y_aligned, 'values') else Y_aligned[valid_mask]
        
        # Reshape data
        X_fit = X_clean.reshape(-1, 1)
        Y_fit = Y_clean.reshape(-1, 1)
        
        # Scale the data
        def robust_scale(data):
            median = np.median(data)
            iqr = np.percentile(data, 75) - np.percentile(data, 25)
            if iqr == 0:
                iqr = np.std(data)
            if iqr == 0:
                iqr = 1.0
            scaled = (data - median) / (iqr + 1e-8)
            return scaled, median, iqr
        
        X_scaled, X_median, X_iqr = robust_scale(X_fit)
        Y_scaled, Y_median, Y_iqr = robust_scale(Y_fit)
        
        # Define kernel and fit model
        kernel = RBF(length_scale=length_scale) + WhiteKernel(noise_level=0.1)
        gpr = GaussianProcessRegressor(
            kernel=kernel,
            random_state=42,
            n_restarts_optimizer=5,
            normalize_y=False
        )
        gpr.fit(X_scaled, Y_scaled.ravel())
        
        def predict_scaled(X_new, scaler_params):
            if np.all(np.isnan(X_new)):
                return np.array([np.nan]), np.array([np.nan])
                
            X_median, X_iqr = scaler_params['X']
            Y_median, Y_iqr = scaler_params['Y']
            
            X_valid = ~np.isnan(X_new)
            X_new_valid = X_new[X_valid]
            X_scaled_new = (X_new_valid - X_median) / (X_iqr + 1e-8)
            
            Y_scaled_pred, Y_scaled_std = gpr.predict(
                X_scaled_new.reshape(-1, 1), 
                return_std=True
            )
            
            full_pred = np.full(X_new.shape, np.nan)
            full_std = np.full(X_new.shape, np.nan)
            
            full_pred[X_valid] = Y_scaled_pred * (Y_iqr + 1e-8) + Y_median
            full_std[X_valid] = Y_scaled_std * (Y_iqr + 1e-8)
            
            return full_pred, full_std
        
        # Make predictions
        scaler_params = {'X': (X_median, X_iqr), 'Y': (Y_median, Y_iqr)}
        current_X = X[2024 if 2024 in X.index else X.index.max()]
        next_year_pred, next_year_std = predict_scaled(np.array([current_X]), scaler_params)
        Y_pred, Y_std = predict_scaled(X_aligned.values, scaler_params)
        
        # Create plot data
        plot_data = pd.DataFrame({
            'X': X_aligned,
            'Y_data': Y_aligned,
            'Y_pred': Y_pred,
            'Y_std': Y_std
        })
        
        # Calculate metrics using only non-NaN values
        valid_metrics = ~np.isnan(Y_pred) & ~np.isnan(Y_aligned)
        metrics = self._calculate_metrics(
            Y_aligned[valid_metrics], 
            Y_pred[valid_metrics], 
            n_params=len(gpr.kernel_.theta)
        )
        
        results = {
            "lag": best_lag,
            "prediction": float(next_year_pred[0]),
            "r2": metrics["r2"],
            "rmse": metrics["rmse"],
            "mae": metrics["mae"],
            "aic": metrics["aic"],
            "std": float(next_year_std[0]),
            "plot_data": plot_data
        }
        
        if do_cv:
            def cv_gpr_model(X, Y):
                valid = ~np.isnan(X) & ~np.isnan(Y)
                X, Y = X[valid], Y[valid]
                
                X = X.reshape(-1, 1)
                Y = Y.reshape(-1, 1)
                X_scaled, X_median, X_iqr = robust_scale(X)
                Y_scaled, Y_median, Y_iqr = robust_scale(Y)
                
                cv_gpr = GaussianProcessRegressor(
                    kernel=kernel.clone_with_theta(kernel.theta),
                    random_state=42,
                    normalize_y=False
                )
                cv_gpr.fit(X_scaled, Y_scaled.ravel())
                
                def predict(X_new):
                    nan_mask = np.isnan(X_new)
                    predictions = np.full(len(X_new), np.nan)
                    if not np.all(nan_mask):
                        X_valid = X_new[~nan_mask].reshape(-1, 1)
                        X_scaled_new = (X_valid - X_median) / (X_iqr + 1e-8)
                        Y_scaled_pred = cv_gpr.predict(X_scaled_new)
                        predictions[~nan_mask] = Y_scaled_pred * (Y_iqr + 1e-8) + Y_median
                    return predictions
                
                return predict
            
            cv_score, cv_error = self._do_cross_validation(
                X_aligned,
                Y_aligned,
                cv_gpr_model,
                {},
                k_folds
            )
            results.update({
                'cv_score': cv_score,
                'cv_error': cv_error
            })
        
        return results