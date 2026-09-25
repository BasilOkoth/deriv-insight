import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
class Command(BaseCommand):
    help='Create/update the admin user from environment variables.'
    def handle(self,*args,**opts):
        username=os.getenv('ADMIN_USERNAME','admin'); email=os.getenv('ADMIN_EMAIL',''); password=os.getenv('ADMIN_PASSWORD','')
        if not password:
            self.stdout.write('ADMIN_PASSWORD not set; skipping admin bootstrap'); return
        User=get_user_model(); u,_=User.objects.get_or_create(username=username,defaults={'email':email,'is_staff':True,'is_superuser':True})
        u.email=email or u.email; u.is_staff=True; u.is_superuser=True; u.set_password(password); u.save(); self.stdout.write(f'Admin ready: {username}')
