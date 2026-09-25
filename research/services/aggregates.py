from .digits import last_digit
from research.models import DigitAggregate

def update_aggregate(symbol,price,pip_size):
    obj,_=DigitAggregate.objects.get_or_create(symbol=symbol,defaults={'pip_size':pip_size,'digit_counts':{str(i):0 for i in range(10)},'transition_counts':{str(i):{str(j):0 for j in range(10)} for i in range(10)}})
    counts=dict(obj.digit_counts or {str(i):0 for i in range(10)})
    trans=dict(obj.transition_counts or {str(i):{str(j):0 for j in range(10)} for i in range(10)})
    d=last_digit(price,pip_size)
    counts[str(d)]=int(counts.get(str(d),0))+1
    if obj.last_digit is not None:
        row=dict(trans.get(str(obj.last_digit),{})); row[str(d)]=int(row.get(str(d),0))+1; trans[str(obj.last_digit)]=row
    obj.pip_size=pip_size; obj.total_ticks=obj.total_ticks+1; obj.digit_counts=counts; obj.transition_counts=trans; obj.last_digit=d; obj.save()
    return obj
