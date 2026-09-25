import json,time
from collections import Counter
from websocket import create_connection,WebSocketTimeoutException
from django.conf import settings
from django.core.management.base import BaseCommand
from research.models import DigitAggregate
from research.services.digits import last_digit

class Command(BaseCommand):
    help='Continuously collect and aggregate Deriv tick last-digits without storing every tick.'
    def add_arguments(self,p):
        p.add_argument('--symbols',default='1HZ100V')
        p.add_argument('--flush',type=int,default=250)
    def handle(self,*args,**opts):
        symbols=[x.strip() for x in opts['symbols'].split(',') if x.strip()]
        flush=max(25,opts['flush'])
        while True:
            try:
                self.run_stream(symbols,flush)
            except KeyboardInterrupt: return
            except Exception as e:
                self.stderr.write(f'collector reconnecting after error: {e}')
                time.sleep(5)
    def run_stream(self,symbols,flush):
        ws=create_connection(settings.DERIV_PUBLIC_WS,timeout=30)
        buffers={s:[] for s in symbols}; pips={}
        try:
            for s in symbols: ws.send(json.dumps({'ticks':s,'subscribe':1}))
            while True:
                try: data=json.loads(ws.recv())
                except WebSocketTimeoutException:
                    ws.send(json.dumps({'ping':1})); continue
                if data.get('error'): raise RuntimeError(data['error'])
                if data.get('msg_type')!='tick': continue
                t=data['tick']; s=t['symbol']; pip=int(t.get('pip_size') or pips.get(s) or 2); pips[s]=pip
                buffers.setdefault(s,[]).append(last_digit(t['quote'],pip))
                if len(buffers[s])>=flush:
                    self.flush(s,pip,buffers[s]); buffers[s]=[]
        finally:
            for s,ds in buffers.items():
                if ds: self.flush(s,pips.get(s,2),ds)
            try: ws.close()
            except Exception: pass
    def flush(self,symbol,pip,ds):
        obj,_=DigitAggregate.objects.get_or_create(symbol=symbol,defaults={'pip_size':pip,'digit_counts':{str(i):0 for i in range(10)},'transition_counts':{str(i):{str(j):0 for j in range(10)} for i in range(10)}})
        counts=dict(obj.digit_counts or {}); trans=dict(obj.transition_counts or {}); prev=obj.last_digit
        for d in ds:
            counts[str(d)]=int(counts.get(str(d),0))+1
            if prev is not None:
                row=dict(trans.get(str(prev),{})); row[str(d)]=int(row.get(str(d),0))+1; trans[str(prev)]=row
            prev=d
        obj.pip_size=pip; obj.total_ticks+=len(ds); obj.digit_counts=counts; obj.transition_counts=trans; obj.last_digit=prev; obj.save()
        self.stdout.write(f'{symbol}: {obj.total_ticks:,} ticks')
