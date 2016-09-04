import sys
import os
import django
if sys.platform == 'darwin':
    sys.path.append('/Users/jeffchen/Documents/gitdir/dashboard')
else:
    sys.path.append('/home/cyh/bigbrother/dashboard')
os.environ["DJANGO_SETTINGS_MODULE"] = "dashboard.settings"
django.setup()
import datetime

import asyncio
import django
import pytz
from tqdm import tqdm

from trader.utils import is_trading_day, update_from_shfe, update_from_dce, update_from_czce, update_from_cffex, \
    create_main_all

if sys.platform == 'darwin':
    sys.path.append('/Users/jeffchen/Documents/gitdir/dashboard')
else:
    sys.path.append('/home/cyh/bigbrother/dashboard')
os.environ["DJANGO_SETTINGS_MODULE"] = "dashboard.settings"
django.setup()

async def fetch_bar():
    day = datetime.datetime.strptime('20160108', '%Y%m%d').replace(tzinfo=pytz.FixedOffset(480))
    end = datetime.datetime.strptime('20160118', '%Y%m%d').replace(tzinfo=pytz.FixedOffset(480))
    tasks = []
    while day <= end:
        tasks.append(is_trading_day(day))
        day += datetime.timedelta(days=1)
    trading_days = []
    for f in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
        rst = await f
        trading_days.append(rst)
    tasks.clear()
    for day, trading in trading_days:
        if trading:
            tasks += [
                asyncio.ensure_future(update_from_shfe(day)),
                asyncio.ensure_future(update_from_dce(day)),
                asyncio.ensure_future(update_from_czce(day)),
                asyncio.ensure_future(update_from_cffex(day)),
            ]
    print('task len=', len(tasks))
    for f in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
        await f

# asyncio.get_event_loop().run_until_complete(fetch_bar())
create_main_all()
