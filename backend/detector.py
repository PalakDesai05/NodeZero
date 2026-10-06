import os, sys, joblib, pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

B = os.path.dirname(os.path.abspath(__file__))
P, D = f'{B}/model.joblib', f'{B}/data/labeled.csv'
_m = None

def train(path=D):
    d = pd.read_csv(path).dropna()
    m = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), stop_words='english', sublinear_tf=True),
                      LogisticRegression(C=10, max_iter=1000, class_weight='balanced'))
    m.fit(d.text, d.label)
    joblib.dump(m, P)
    print(f'trained on {len(d)} rows')
    return m

def score(texts):
    """Probability (0-1) that each text is misinformation. Label 1 = misinformation."""
    global _m
    _m = _m or (joblib.load(P) if os.path.exists(P) else train())
    return _m.predict_proba([str(t) for t in texts])[:, 1]

if __name__ == '__main__':
    train(sys.argv[1] if len(sys.argv) > 1 else D)
