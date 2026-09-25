from __future__ import annotations
import math
from collections import Counter
from decimal import Decimal

DIGIT_TYPES={'DIGITMATCH','DIGITDIFF','DIGITOVER','DIGITUNDER','DIGITEVEN','DIGITODD'}

def last_digit(price,pip_size):
    q=Decimal(str(price))
    s=f"{q:.{int(pip_size)}f}"
    return int(s[-1])

def digits_from_prices(prices,pip_size):
    return [last_digit(p,pip_size) for p in prices]

def wilson(k,n,z=1.96):
    if n<=0: return (0.0,1.0)
    p=k/n; d=1+z*z/n
    c=(p+z*z/(2*n))/d
    h=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/d
    return max(0,c-h),min(1,c+h)

def outcome_hit(d,contract_type,barrier=None):
    if contract_type=='DIGITMATCH': return d==int(barrier)
    if contract_type=='DIGITDIFF': return d!=int(barrier)
    if contract_type=='DIGITOVER': return d>int(barrier)
    if contract_type=='DIGITUNDER': return d<int(barrier)
    if contract_type=='DIGITEVEN': return d%2==0
    if contract_type=='DIGITODD': return d%2==1
    raise ValueError('Unsupported digit contract')

def analyze_digits(digits:list[int]):
    n=len(digits); counts=Counter(digits)
    freqs={str(i):(counts[i]/n if n else 0) for i in range(10)}
    expected=n/10 if n else 0
    chi2=sum(((counts[i]-expected)**2/expected) for i in range(10)) if expected else 0
    entropy=-sum(p*math.log2(p) for p in freqs.values() if p>0)
    transitions={str(i):{str(j):0 for j in range(10)} for i in range(10)}
    for a,b in zip(digits,digits[1:]): transitions[str(a)][str(b)]+=1
    even=sum(counts[i] for i in [0,2,4,6,8])
    same=sum(1 for a,b in zip(digits,digits[1:]) if a==b)
    return {
        'ticks':n,'counts':{str(i):counts[i] for i in range(10)},'frequencies':freqs,
        'chi_square':chi2,'entropy_bits':entropy,'even_pct':even/n*100 if n else 0,
        'odd_pct':(n-even)/n*100 if n else 0,'repeat_pct':same/max(n-1,1)*100,
        'transitions':transitions,'last_digit':digits[-1] if digits else None,
    }

def empirical_probability(digits,contract_type,barrier=None,conditional=True,shrink=200):
    if not digits: return {'p':0,'lower':0,'upper':1,'n':0,'global_p':0,'conditional_p':None,'conditional_n':0}
    global_hits=sum(outcome_hit(d,contract_type,barrier) for d in digits)
    global_p=global_hits/len(digits)
    p=global_p; effective_n=len(digits); conditional_p=None; conditional_n=0
    if conditional and len(digits)>2:
        prev=digits[-1]
        nexts=[b for a,b in zip(digits[:-1],digits[1:]) if a==prev]
        conditional_n=len(nexts)
        if conditional_n:
            ch=sum(outcome_hit(d,contract_type,barrier) for d in nexts)
            conditional_p=ch/conditional_n
            w=conditional_n/(conditional_n+shrink)
            p=(1-w)*global_p+w*conditional_p
            effective_n=len(digits)+conditional_n
    # conservative uncertainty uses global sample size, not inflated effective_n
    lo,hi=wilson(global_hits,len(digits))
    return {'p':p,'lower':lo,'upper':hi,'n':len(digits),'global_p':global_p,'conditional_p':conditional_p,'conditional_n':conditional_n}

def proposal_evaluation(*,digits,contract_type,barrier,ask_price,payout,min_edge_pp=0.75,min_ticks=5000,min_confidence_margin_pp=0.0):
    ask=float(ask_price or 0); pay=float(payout or 0)
    be=(ask/pay) if pay>0 else 1.0
    est=empirical_probability(digits,contract_type,barrier)
    edge=(est['p']-be)*100
    lower_edge=(est['lower']-be)*100
    research_candidate=len(digits)>=min_ticks and edge>=min_edge_pp
    demo_candidate=research_candidate and lower_edge>=float(min_confidence_margin_pp)
    return {
        **est,'break_even':be,'edge_pp':edge,'lower_edge_pp':lower_edge,
        'research_candidate':research_candidate,'demo_candidate':demo_candidate,
        'decision':'DEMO CANDIDATE' if demo_candidate else ('RESEARCH' if research_candidate else 'NO TRADE'),
    }

def threshold_backtest(digits,contract_type,barrier,profit_return_pct=792.9,warmup=1000,window=3000,min_edge_pp=0.75):
    profit=float(profit_return_pct)/100.0
    warmup=max(200,int(warmup)); window=max(warmup,int(window))
    trades=[]
    for i in range(warmup,len(digits)-1):
        hist=digits[max(0,i-window):i+1]
        est=empirical_probability(hist,contract_type,barrier)
        be=1/(1+profit)
        edge=(est['p']-be)*100
        if edge < min_edge_pp: continue
        won=outcome_hit(digits[i+1],contract_type,barrier)
        pnl=profit if won else -1.0
        trades.append({'i':i,'p':est['p'],'be':be,'edge_pp':edge,'won':won,'pnl':pnl,'next_digit':digits[i+1]})
    wins=sum(t['won'] for t in trades); total=sum(t['pnl'] for t in trades)
    return {
        'trades':len(trades),'wins':wins,'win_rate_pct':wins/len(trades)*100 if trades else 0,
        'net_units':total,'expectancy_units':total/len(trades) if trades else 0,
        'break_even_pct':1/(1+profit)*100,'profit_per_win_units':profit,
        'sample':trades[-100:],
    }
