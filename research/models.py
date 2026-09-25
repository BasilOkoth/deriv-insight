from django.db import models

class RiskConfig(models.Model):
    name=models.CharField(max_length=32,unique=True,default='primary')
    default_symbol=models.CharField(max_length=32,default='1HZ100V')
    history_ticks=models.PositiveIntegerField(default=5000)
    stake_usd=models.FloatField(default=1.0)
    min_research_ticks=models.PositiveIntegerField(default=5000)
    min_demo_ticks=models.PositiveIntegerField(default=10000)
    min_edge_pp=models.FloatField(default=0.75)
    min_confidence_margin_pp=models.FloatField(default=0.0)
    max_daily_loss_usd=models.FloatField(default=10.0)
    max_consecutive_losses=models.PositiveIntegerField(default=5)
    max_demo_trades_per_day=models.PositiveIntegerField(default=20)
    demo_execution_enabled=models.BooleanField(default=False)
    updated_at=models.DateTimeField(auto_now=True)
    @classmethod
    def current(cls):
        obj,_=cls.objects.get_or_create(name='primary')
        return obj

class DigitAggregate(models.Model):
    symbol=models.CharField(max_length=32,unique=True)
    pip_size=models.PositiveSmallIntegerField(default=2)
    total_ticks=models.BigIntegerField(default=0)
    digit_counts=models.JSONField(default=dict)
    transition_counts=models.JSONField(default=dict)
    last_digit=models.PositiveSmallIntegerField(null=True,blank=True)
    updated_at=models.DateTimeField(auto_now=True)

class ResearchRun(models.Model):
    symbol=models.CharField(max_length=32)
    contract_type=models.CharField(max_length=24)
    barrier=models.CharField(max_length=8,blank=True)
    ticks=models.PositiveIntegerField(default=0)
    assumed_return_pct=models.FloatField(default=0)
    trades=models.PositiveIntegerField(default=0)
    win_rate_pct=models.FloatField(default=0)
    net_units=models.FloatField(default=0)
    expectancy_units=models.FloatField(default=0)
    results=models.JSONField(default=dict)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']

class ProposalSnapshot(models.Model):
    symbol=models.CharField(max_length=32)
    contract_type=models.CharField(max_length=24)
    barrier=models.CharField(max_length=8,blank=True)
    stake=models.FloatField(default=1)
    ask_price=models.FloatField()
    payout=models.FloatField()
    break_even_pct=models.FloatField()
    model_probability_pct=models.FloatField(default=0)
    lower95_pct=models.FloatField(default=0)
    edge_pp=models.FloatField(default=0)
    sample_ticks=models.PositiveIntegerField(default=0)
    decision=models.CharField(max_length=24,default='NO TRADE')
    observed_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-observed_at']

class DemoTrade(models.Model):
    symbol=models.CharField(max_length=32)
    contract_type=models.CharField(max_length=24)
    barrier=models.CharField(max_length=8,blank=True)
    stake=models.FloatField(default=1)
    proposal_id=models.CharField(max_length=128,blank=True)
    contract_id=models.CharField(max_length=128,blank=True)
    status=models.CharField(max_length=24,default='blocked')
    pnl=models.FloatField(null=True,blank=True)
    reason=models.TextField(blank=True)
    metadata=models.JSONField(default=dict)
    opened_at=models.DateTimeField(auto_now_add=True)
    closed_at=models.DateTimeField(null=True,blank=True)
    class Meta: ordering=['-opened_at']
