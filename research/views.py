from __future__ import annotations
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render,redirect
from django.utils import timezone
from django.conf import settings
from .forms import DigitLabForm,EdgeForm,BacktestForm,RiskConfigForm
from .models import RiskConfig,ResearchRun,ProposalSnapshot,DemoTrade,DigitAggregate
from .services.deriv_client import DerivPublicClient,DerivDemoClient,DerivAPIError
from .services.digits import digits_from_prices,analyze_digits,proposal_evaluation,threshold_backtest

BARRIER_TYPES={'DIGITMATCH','DIGITDIFF','DIGITOVER','DIGITUNDER'}

def _history(symbol,ticks):
    data=DerivPublicClient().ticks_history(symbol,int(ticks))
    digits=digits_from_prices(data['prices'],data['pip_size'])
    return data,digits

def _symbols():
    try:
        rows=DerivPublicClient().active_symbols()
        out=[]
        for r in rows:
            code=r.get('underlying_symbol') or r.get('symbol')
            name=r.get('underlying_symbol_name') or r.get('display_name') or code
            market=(r.get('market') or '').lower(); sub=(r.get('submarket') or '').lower()
            if code and ('synthetic' in market or 'synthetic' in sub or code.startswith(('R_','1HZ','JD','BOOM','CRASH'))):
                out.append({'code':code,'name':name or code,'pip_size':r.get('pip_size') or r.get('pip')})
        return out[:200]
    except Exception:
        return []

@login_required
def overview(request):
    cfg=RiskConfig.current(); api_ok=False; symbols=[]; error=''
    try:
        symbols=_symbols(); api_ok=bool(symbols)
    except Exception as e: error=str(e)
    latest=ProposalSnapshot.objects.all()[:8]
    return render(request,'research/overview.html',{
        'cfg':cfg,'api_ok':api_ok,'symbols':symbols[:12],'symbol_count':len(symbols),'error':error,
        'runs':ResearchRun.objects.all()[:6],'proposals':latest,'aggregates':DigitAggregate.objects.all().order_by('-total_ticks')[:8],
        'demo_env_enabled':settings.DERIV_DEMO_ENABLED,'real_enabled':settings.DERIV_REAL_ENABLED,
    })

@login_required
def digit_lab(request):
    cfg=RiskConfig.current(); form=DigitLabForm(request.POST or None,initial={'symbol':cfg.default_symbol,'ticks':min(cfg.history_ticks,25000)})
    result=None; error=''; symbol_choices=_symbols()
    if request.method=='POST' and form.is_valid():
        try:
            data,digits=_history(form.cleaned_data['symbol'],form.cleaned_data['ticks'])
            result=analyze_digits(digits); result['pip_size']=data['pip_size']; result['symbol']=form.cleaned_data['symbol']
        except Exception as e: error=str(e)
    return render(request,'research/digits.html',{'form':form,'result':result,'error':error,'symbol_choices':symbol_choices})

@login_required
def edge_radar(request):
    cfg=RiskConfig.current(); form=EdgeForm(request.POST or None,initial={'symbol':cfg.default_symbol,'stake':cfg.stake_usd,'ticks':min(cfg.history_ticks,25000),'contract_type':'DIGITMATCH','barrier':'7'})
    result=None; error=''; proposal=None; history=None
    if request.method=='POST' and form.is_valid():
        try:
            symbol=form.cleaned_data['symbol']; ctype=form.cleaned_data['contract_type']; barrier=form.cleaned_data.get('barrier') if ctype in BARRIER_TYPES else None
            data,digits=_history(symbol,form.cleaned_data['ticks']); history=analyze_digits(digits)
            proposal=DerivPublicClient().proposal(symbol=symbol,contract_type=ctype,stake=float(form.cleaned_data['stake']),barrier=barrier)
            ask=float(proposal.get('ask_price') or form.cleaned_data['stake']); payout=float(proposal.get('payout') or 0)
            result=proposal_evaluation(digits=digits,contract_type=ctype,barrier=barrier,ask_price=ask,payout=payout,min_edge_pp=cfg.min_edge_pp,min_ticks=cfg.min_research_ticks,min_confidence_margin_pp=cfg.min_confidence_margin_pp)
            ProposalSnapshot.objects.create(symbol=symbol,contract_type=ctype,barrier=barrier or '',stake=float(form.cleaned_data['stake']),ask_price=ask,payout=payout,break_even_pct=result['break_even']*100,model_probability_pct=result['p']*100,lower95_pct=result['lower']*100,edge_pp=result['edge_pp'],sample_ticks=len(digits),decision=result['decision'])
            result.update({'symbol':symbol,'contract_type':ctype,'barrier':barrier,'ask_price':ask,'payout':payout,'proposal_id':proposal.get('id')})
        except Exception as e: error=str(e)
    return render(request,'research/radar.html',{'form':form,'result':result,'history':history,'proposal':proposal,'error':error,'cfg':cfg})

@login_required
def backtests(request):
    cfg=RiskConfig.current(); form=BacktestForm(request.POST or None,initial={'symbol':cfg.default_symbol,'ticks':5000,'assumed_return_pct':792.9,'min_edge_pp':cfg.min_edge_pp,'contract_type':'DIGITMATCH','barrier':'7'})
    result=None; error=''
    if request.method=='POST' and form.is_valid():
        try:
            symbol=form.cleaned_data['symbol']; ctype=form.cleaned_data['contract_type']; barrier=form.cleaned_data.get('barrier') if ctype in BARRIER_TYPES else None
            data,digits=_history(symbol,form.cleaned_data['ticks'])
            result=threshold_backtest(digits,ctype,barrier,float(form.cleaned_data['assumed_return_pct']),warmup=min(1000,max(200,len(digits)//4)),window=min(3000,len(digits)),min_edge_pp=float(form.cleaned_data['min_edge_pp']))
            ResearchRun.objects.create(symbol=symbol,contract_type=ctype,barrier=barrier or '',ticks=len(digits),assumed_return_pct=float(form.cleaned_data['assumed_return_pct']),trades=result['trades'],win_rate_pct=result['win_rate_pct'],net_units=result['net_units'],expectancy_units=result['expectancy_units'],results=result)
            result.update({'symbol':symbol,'contract_type':ctype,'barrier':barrier})
        except Exception as e: error=str(e)
    return render(request,'research/backtests.html',{'form':form,'result':result,'error':error,'runs':ResearchRun.objects.all()[:30]})

def _demo_gate(cfg):
    today=timezone.localdate(); qs=DemoTrade.objects.filter(opened_at__date=today)
    pnl=qs.filter(pnl__isnull=False).aggregate(v=Sum('pnl'))['v'] or 0
    closed=list(qs.filter(pnl__isnull=False).order_by('-opened_at')[:cfg.max_consecutive_losses])
    loss_streak=0
    for t in closed:
        if (t.pnl or 0)<0: loss_streak+=1
        else: break
    reasons=[]
    if not settings.DERIV_DEMO_ENABLED: reasons.append('DERIV_DEMO_ENABLED is false')
    if not cfg.demo_execution_enabled: reasons.append('demo execution is disabled in Risk Settings')
    if qs.count()>=cfg.max_demo_trades_per_day: reasons.append('daily demo trade limit reached')
    if pnl<=-abs(cfg.max_daily_loss_usd): reasons.append('daily loss limit reached')
    if loss_streak>=cfg.max_consecutive_losses: reasons.append('consecutive-loss circuit breaker reached')
    if not settings.DERIV_AUTH_TOKEN or not settings.DERIV_ACCOUNT_ID: reasons.append('demo API credentials are not configured')
    return {'passes':not reasons,'reasons':reasons,'daily_pnl':float(pnl),'trades_today':qs.count(),'loss_streak':loss_streak}

@login_required
def demo_lab(request):
    cfg=RiskConfig.current(); gate=_demo_gate(cfg); error=''
    if request.method=='POST':
        try:
            symbol=request.POST.get('symbol','').strip(); ctype=request.POST.get('contract_type','').strip(); barrier=request.POST.get('barrier','').strip() or None; stake=float(request.POST.get('stake') or cfg.stake_usd)
            # Fresh public evidence is mandatory immediately before every demo order.
            data,digits=_history(symbol,min(max(cfg.min_demo_ticks,1000),25000))
            prop=DerivPublicClient().proposal(symbol=symbol,contract_type=ctype,stake=stake,barrier=barrier)
            ev=proposal_evaluation(digits=digits,contract_type=ctype,barrier=barrier,ask_price=float(prop.get('ask_price') or stake),payout=float(prop.get('payout') or 0),min_edge_pp=cfg.min_edge_pp,min_ticks=cfg.min_demo_ticks,min_confidence_margin_pp=cfg.min_confidence_margin_pp)
            if not gate['passes']: raise DerivAPIError('; '.join(gate['reasons']))
            if not ev['demo_candidate']: raise DerivAPIError(f"Fresh evidence failed demo gate: edge {ev['edge_pp']:.2f}pp, lower-edge {ev['lower_edge_pp']:.2f}pp")
            trade=DemoTrade.objects.create(symbol=symbol,contract_type=ctype,barrier=barrier or '',stake=stake,status='opening',metadata={'pretrade_evidence':ev})
            outcome=DerivDemoClient().trade_digit(symbol=symbol,contract_type=ctype,stake=stake,barrier=barrier,model_probability=ev['p'],lower_probability=ev['lower'],min_edge_pp=cfg.min_edge_pp,min_confidence_margin_pp=cfg.min_confidence_margin_pp)
            buy=outcome.get('buy') or {}; settled=outcome.get('settled') or {}
            trade.proposal_id=str((outcome.get('proposal') or {}).get('id') or ''); trade.contract_id=str(buy.get('contract_id') or '')
            trade.status=str(settled.get('status') or ('open' if buy else 'failed'))
            if settled.get('profit') is not None: trade.pnl=float(settled.get('profit'))
            if trade.pnl is not None: trade.closed_at=timezone.now()
            trade.metadata={**trade.metadata,'buy':buy,'settled':settled}; trade.save()
            messages.success(request,f"Demo contract completed: {trade.status}, P/L {trade.pnl if trade.pnl is not None else 'pending'}")
            return redirect('demo_lab')
        except Exception as e: error=str(e); messages.error(request,error)
    gate=_demo_gate(cfg)
    return render(request,'research/demo.html',{'cfg':cfg,'gate':gate,'trades':DemoTrade.objects.all()[:100],'error':error,'env_demo':settings.DERIV_DEMO_ENABLED})

@login_required
def settings_view(request):
    cfg=RiskConfig.current(); form=RiskConfigForm(request.POST or None,instance=cfg)
    if request.method=='POST' and form.is_valid():
        form.save(); messages.success(request,'Risk settings saved.'); return redirect('settings')
    return render(request,'research/settings.html',{'form':form,'cfg':cfg,'demo_env':settings.DERIV_DEMO_ENABLED,'real_enabled':settings.DERIV_REAL_ENABLED})

def health(request): return JsonResponse({'ok':True,'service':'Deriv Insight','real_trading_enabled':False})
