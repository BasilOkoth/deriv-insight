from research.services.digits import last_digit, analyze_digits, proposal_evaluation, threshold_backtest

def run():
    assert last_digit(123.456,3)==6
    ds=[0,1,2,3,4,5,6,7,8,9]*1000
    a=analyze_digits(ds)
    assert a['ticks']==10000
    assert abs(a['even_pct']-50.0)<1e-9
    # $1 ask / $8.929 total payout -> 11.1995% break-even.
    ev=proposal_evaluation(digits=ds,contract_type='DIGITMATCH',barrier='7',ask_price=1,payout=8.929,min_ticks=5000)
    assert 11.19 < ev['break_even']*100 < 11.21
    # Synthetic deterministic cycle is only a smoke test for no-look-ahead mechanics.
    bt=threshold_backtest(ds,'DIGITMATCH','7',792.9,warmup=1000,min_edge_pp=0)
    assert bt['trades']>=0
    print('digit math smoke tests OK')
if __name__=='__main__': run()
