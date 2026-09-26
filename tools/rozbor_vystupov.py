import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from smc_bot.trade_forensics import load_trades
tr=load_trades("/root/.claude/uploads/74290846-7b41-53bb-a543-50a76081d336/241e2225-trades_2026-09-26.xlsx","XAU/USD","UTC")
m=pd.read_parquet("data/XAU_USD_M1_dukascopy.parquet").sort_index()
mid=((m.bid_close+m.ask_close)/2).to_numpy(); hi=m.ask_high.to_numpy(); lo=m.bid_low.to_numpy(); idx=m.index
R=[]
for _, r in tr.iterrows():
    a=idx.searchsorted(r.t); b=max(idx.searchsorted(r.t_close),a+1); d=r.dir; e=r["Open price"]
    path_hi, path_lo = hi[a:b+1].max(), lo[a:b+1].min()
    mfe = (path_hi-e) if d==1 else (e-path_lo); mae=(e-path_lo) if d==1 else (path_hi-e)
    ext = (hi[a:b+1].argmax() if d==1 else lo[a:b+1].argmin())  # minúta najlepšej ceny
    after=[d*(mid[min(b+n,len(mid)-1)]-mid[b]) for n in (5,15,60)]
    # trend pohybu tesne pred výstupom (posledných 3 min) v smere obchodu
    pre=d*(mid[b-1]-mid[max(b-4,a)]) if b-1>a else 0
    R.append(dict(pts=r.pts,mfe=mfe,mae=mae,giveback=mfe-r.pts,min=(b-a),min_to_ext=ext,min_ext_to_exit=(b-a)-ext,
                  po5=after[0],po15=after[1],po60=after[2],pred_vystupom_3m=pre,
                  cents=round(r["Close price"]%1,2), dist5=min(r["Close price"]%5,5-r["Close price"]%5)))
X=pd.DataFrame(R); W=X[X.pts>0]; L=X[X.pts<=0]
q=lambda s: f"{s.median():6.2f}"
print("medián           zisky   straty")
for c in ["pts","mfe","mae","giveback","min","min_to_ext","min_ext_to_exit","pred_vystupom_3m","po5","po15","po60","dist5"]:
    print(f"{c:18s}{q(W[c])} {q(L[c])}")
print("zisky: vybraté % z MFE (medián):", round((W.pts/W.mfe).median()*100), "%")
print("zisky: po výstupe cena pokračovala v smere (po 15 min >0):", f"{100*(W.po15>0).mean():.0f} %", "| straty: po výstupe sa otočila v jeho prospech:", f"{100*(L.po15>0).mean():.0f} %")
print("straty: MFE pred stratou >= 2 USD:", f"{100*(L.mfe>=2).mean():.0f} %", "| strata / MAE (medián):", round((-L.pts/L.mae).median(),2))
print("výstup pri pohybe proti obchodu v posledných 3 min:", f"zisky {100*(W.pred_vystupom_3m<0).mean():.0f} %, straty {100*(L.pred_vystupom_3m<0).mean():.0f} %")
print("rozdelenie zisku USD:", pd.cut(W.pts,[0,1,2,4,8,15,100]).value_counts().sort_index().to_dict())
print("rozdelenie straty USD:", pd.cut(-L.pts,[0,2,4,8,15,30,100]).value_counts().sort_index().to_dict())
print("\n== napodobnenie výstupov na jeho vstupoch (TP / stop / výstup po odraze od najhoršieho bodu)")
bid_o=m.bid_open.to_numpy(); ask_o=m.ask_open.to_numpy(); bh=m.bid_high.to_numpy(); bl=m.bid_low.to_numpy(); ah=m.ask_high.to_numpy(); al=m.ask_low.to_numpy()
def sim(t,d,tp,sl,bounce_after,bounce,mx=240):
    j=idx.searchsorted(t); e=ask_o[j] if d==1 else bid_o[j]; worst=0
    for k in range(j,min(j+mx,len(idx)-1)):
        lo_,hi_=(bl[k],bh[k]) if d==1 else (al[k],ah[k])
        adv=(e-lo_) if d==1 else (hi_-e); fav=(hi_-e) if d==1 else (e-lo_)
        if adv>=sl: return -sl
        if k>j and fav>=tp: return tp
        worst=max(worst,adv)
        cur=d*(((bid_o[k+1]) if d==1 else ask_o[k+1])-e)
        if bounce_after and worst>=bounce_after and cur>=-worst+bounce: return cur
    return d*((bid_o[k] if d==1 else ask_o[k])-e)
for tp,sl,ba,bo in ((4,12,0,0),(5,12,0,0),(4,12,6,1.5),(5,12,6,1.5),(5,15,8,2),(6,12,6,2),(4,20,8,2)):
    r=np.array([sim(t,d,tp,sl,ba,bo) for t,d in zip(tr.t,tr.dir)])
    print(f"TP {tp} SL {sl} odraz(po -{ba}, +{bo}): win {100*(r>0).mean():.0f} %, PF {r[r>0].sum()/-r[r<=0].sum():.2f}, priem. {r.mean():+.2f}")
