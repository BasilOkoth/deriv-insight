from django import forms
from .models import RiskConfig

CONTRACT_CHOICES=[
 ('DIGITMATCH','Digit Match'),('DIGITDIFF','Digit Differs'),('DIGITOVER','Digit Over'),('DIGITUNDER','Digit Under'),('DIGITEVEN','Even'),('DIGITODD','Odd')
]

class DigitLabForm(forms.Form):
    symbol=forms.CharField(initial='1HZ100V',max_length=32)
    ticks=forms.ChoiceField(choices=[('1000','1,000'),('2500','2,500'),('5000','5,000'),('10000','10,000'),('25000','25,000')],initial='5000')

class EdgeForm(forms.Form):
    symbol=forms.CharField(initial='1HZ100V',max_length=32)
    contract_type=forms.ChoiceField(choices=CONTRACT_CHOICES,initial='DIGITMATCH')
    barrier=forms.ChoiceField(choices=[(str(i),str(i)) for i in range(10)],initial='7',required=False)
    stake=forms.DecimalField(min_value=0.35,initial=1,decimal_places=2,max_digits=8)
    ticks=forms.ChoiceField(choices=[('1000','1,000'),('2500','2,500'),('5000','5,000'),('10000','10,000'),('25000','25,000')],initial='5000')

class BacktestForm(forms.Form):
    symbol=forms.CharField(initial='1HZ100V',max_length=32)
    contract_type=forms.ChoiceField(choices=CONTRACT_CHOICES,initial='DIGITMATCH')
    barrier=forms.ChoiceField(choices=[(str(i),str(i)) for i in range(10)],initial='7',required=False)
    ticks=forms.ChoiceField(choices=[('2500','2,500'),('5000','5,000'),('10000','10,000'),('25000','25,000')],initial='5000')
    assumed_return_pct=forms.DecimalField(initial=792.9,min_value=1,max_value=10000,decimal_places=2,max_digits=8)
    min_edge_pp=forms.DecimalField(initial=0.75,min_value=0,max_value=20,decimal_places=2,max_digits=5)

class RiskConfigForm(forms.ModelForm):
    class Meta:
        model=RiskConfig
        fields=['default_symbol','history_ticks','stake_usd','min_research_ticks','min_demo_ticks','min_edge_pp','min_confidence_margin_pp','max_daily_loss_usd','max_consecutive_losses','max_demo_trades_per_day','demo_execution_enabled']
