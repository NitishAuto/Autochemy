import pandas as pd

def sys(X, y, n_features):
    scores = {}
    for col in X.columns:
        corr = dcor.distance_correlation(X[col].values, y.values)
        scores[col] = corr
    
    scores = pd.Series(scores).sort_values(ascending=False)
    selected_features = scores.head(top_k).index.tolist()
    return selected_features