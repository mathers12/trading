import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import cross_val_score, StratifiedKFold
from smc_bot.trade_forensics import load_trades, features
tr=load_trades("/root/.claude/uploads/74290846-7b41-53bb-a543-50a76081d336/241e2225-trades_2026-09-26.xlsx","XAU/USD","UTC")
m=pd.read_parquet("data/XAU_USD_M1_dukascopy.parquet").sort_index()
rng=np.random.default_rng(1); rows=[]
for _,r in tr.iterrows():
    f=features(m,r.t,r.dir)
    if f: rows.append({**f,"y":1,"hod":r.t.hour,"win":r.pts>0})
days=sorted({t.normalize() for t in tr.t})
for _ in range(1500):
    t=days[rng.integers(len(days))]+pd.Timedelta(minutes=int(rng.integers(24*60)))
    f=features(m,t,int(rng.choice([1,-1])))
    if f: rows.append({**f,"y":0,"hod":t.hour,"win":False})
D=pd.DataFrame(rows).fillna(0).astype(float)
X=D.drop(columns=["y","win"]); y=D.y
cv=StratifiedKFold(5,shuffle=True,random_state=0)
gb=GradientBoostingClassifier(n_estimators=150,max_depth=2,learning_rate=0.05,subsample=0.8,random_state=0)
auc=cross_val_score(gb,X,y,cv=cv,scoring="roc_auc")
print(f"vstup vs náhodný okamih (smer aj čas): AUC {auc.mean():.2f} ± {auc.std():.2f}  (0,5 = nerozlíšiteľné)")
gb.fit(X,y); imp=pd.Series(gb.feature_importances_,X.columns).sort_values(ascending=False)
print("najdôležitejšie:", ", ".join(f"{k} {v:.2f}" for k,v in imp.head(6).items()))
E=D[D.y==1]; Xe=E.drop(columns=["y","win"]); ye=E.win
auc2=cross_val_score(GradientBoostingClassifier(n_estimators=100,max_depth=2,learning_rate=0.05,random_state=0),Xe,ye,cv=StratifiedKFold(5,shuffle=True,random_state=0),scoring="roc_auc")
print(f"zisk vs strata medzi jeho obchodmi: AUC {auc2.mean():.2f} ± {auc2.std():.2f}")
