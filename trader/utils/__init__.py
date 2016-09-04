# coding=utf-8
#
# Copyright 2016 timercrack
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
from decimal import Decimal
import datetime
import math
import re
import xml.etree.ElementTree as ET
import asyncio

import pytz
from bs4 import BeautifulSoup
import aiohttp
from django.db.models import F

from panel.models import *
from trader.utils import ApiStruct

max_conn_shfe = asyncio.Semaphore(15)
max_conn_dce = asyncio.Semaphore(5)
max_conn_czce = asyncio.Semaphore(15)
max_conn_cffex = asyncio.Semaphore(15)


def str_to_number(s):
    try:
        if not isinstance(s, str):
            return s
        return int(s)
    except ValueError:
        return float(s)


def myround(x: Decimal, base: Decimal):
    prec = 0
    s = str(round(base, 3) % 1)
    s = s.rstrip('0').rstrip('.') if '.' in s else s
    p1, *p2 = s.split('.')
    if p2:
        prec = len(p2[0])
    return round(base * round(x / base), prec)


async def is_trading_day(day: datetime.datetime = datetime.datetime.today()):
    """
    判断是否是交易日, 方法是从中金所获取今日的K线数据,判断http的返回码(如果出错会返回302重定向至404页面),
    因为开市前也可能返回302, 所以适合收市后(下午)使用
    :return: bool
    """
    async with aiohttp.ClientSession() as session:
        await max_conn_cffex.acquire()
        async with session.get(
                'http://www.cffex.com.cn/fzjy/mrhq/{}/index.xml'.format(day.strftime('%Y%m/%d')),
                allow_redirects=False) as response:
            max_conn_cffex.release()
            return day, response.status != 302


def calc_expire_date(inst_code: str, day: datetime.datetime):
    expire_date = int(re.findall('\d+', inst_code)[0])
    if expire_date < 1000:
        year_exact = math.floor(day.year % 100 / 10)
        if expire_date < 100 and day.year % 10 == 9:
            year_exact += 1
        expire_date += year_exact * 1000
    return expire_date

async def update_from_shfe(day: datetime.datetime):
    async with aiohttp.ClientSession() as session:
        day_str = day.strftime('%Y%m%d')
        await max_conn_shfe.acquire()
        async with session.get('http://www.shfe.com.cn/data/dailydata/kx/kx{}.dat'.format(day_str)) as response:
            rst_json = await response.json()
            max_conn_shfe.release()
            for inst_data in rst_json['o_curinstrument']:
                """
    {'OPENINTERESTCHG': -11154, 'CLOSEPRICE': 36640, 'SETTLEMENTPRICE': 36770, 'OPENPRICE': 36990,
    'PRESETTLEMENTPRICE': 37080, 'ZD2_CHG': -310, 'DELIVERYMONTH': '1609', 'VOLUME': 51102,
    'PRODUCTSORTNO': 10, 'ZD1_CHG': -440, 'OPENINTEREST': 86824, 'ORDERNO': 0, 'PRODUCTNAME': '铜                  ',
    'LOWESTPRICE': 36630, 'PRODUCTID': 'cu_f    ', 'HIGHESTPRICE': 37000}
                """
                # error_data = inst_data
                if inst_data['DELIVERYMONTH'] == '小计' or inst_data['PRODUCTID'] == '总计':
                    continue
                if '_' not in inst_data['PRODUCTID']:
                    continue
                DailyBar.objects.update_or_create(
                    code=inst_data['PRODUCTID'].split('_')[0] + inst_data['DELIVERYMONTH'],
                    exchange=ExchangeType.SHFE, time=day, defaults={
                        'expire_date': inst_data['DELIVERYMONTH'],
                        'open': inst_data['OPENPRICE'] if inst_data['OPENPRICE'] else inst_data['CLOSEPRICE'],
                        'high': inst_data['HIGHESTPRICE'] if inst_data['HIGHESTPRICE'] else
                        inst_data['CLOSEPRICE'],
                        'low': inst_data['LOWESTPRICE'] if inst_data['LOWESTPRICE']
                        else inst_data['CLOSEPRICE'],
                        'close': inst_data['CLOSEPRICE'],
                        'settlement': inst_data['SETTLEMENTPRICE'] if inst_data['SETTLEMENTPRICE'] else
                        inst_data['PRESETTLEMENTPRICE'],
                        'volume': inst_data['VOLUME'] if inst_data['VOLUME'] else 0,
                        'open_interest': inst_data['OPENINTEREST'] if inst_data['OPENINTEREST'] else 0})

async def fetch_czce_page(session, url):
    await max_conn_czce.acquire()
    rst = None
    async with session.get(url) as response:
        if response.status == 200:
            rst = await response.text(encoding='gbk')
    max_conn_czce.release()
    return rst


async def update_from_czce(day: datetime.datetime):
    async with aiohttp.ClientSession() as session:
        day_str = day.strftime('%Y%m%d')
        rst = await fetch_czce_page(
            session, 'http://www.czce.com.cn/portal/DFSStaticFiles/Future/{}/{}/FutureDataDaily.txt'.format(
                    day.year, day_str))
        if rst is None:
            rst = await fetch_czce_page(
                session, 'http://www.czce.com.cn/portal/exchange/{}/datadaily/{}.txt'.format(
                        day.year, day_str))
        for lines in rst.split('\r\n')[1:-3]:
            if '小计' in lines or '品种' in lines:
                continue
            inst_data = [x.strip() for x in lines.split('|' if '|' in lines else ',')]
            # error_data = inst_data
            """
[0'品种月份', 1'昨结算', 2'今开盘', 3'最高价', 4'最低价', 5'今收盘', 6'今结算', 7'涨跌1', 8'涨跌2', 9'成交量(手)', 10'空盘量', 11'增减量', 12'成交额(万元)', 13'交割结算价']
['CF601', '11,970.00', '11,970.00', '11,970.00', '11,800.00', '11,870.00', '11,905.00', '-100.00',
'-65.00', '13,826', '59,140', '-10,760', '82,305.24', '']
            """
            DailyBar.objects.update_or_create(
                code=inst_data[0],
                exchange=ExchangeType.CZCE, time=day, defaults={
                    'expire_date': calc_expire_date(inst_data[0], day),
                    'open': inst_data[2].replace(',', '') if Decimal(inst_data[2].replace(',', '')) > 0.1
                    else inst_data[5].replace(',', ''),
                    'high': inst_data[3].replace(',', '') if Decimal(inst_data[3].replace(',', '')) > 0.1
                    else inst_data[5].replace(',', ''),
                    'low': inst_data[4].replace(',', '') if Decimal(inst_data[4].replace(',', '')) > 0.1
                    else inst_data[5].replace(',', ''),
                    'close': inst_data[5].replace(',', ''),
                    'settlement': inst_data[6].replace(',', '') if Decimal(inst_data[6].replace(',', '')) > 0.1 else
                    inst_data[1].replace(',', ''),
                    'volume': inst_data[9].replace(',', ''),
                    'open_interest': inst_data[10].replace(',', '')})


async def update_from_dce(day: datetime.datetime):
    async with aiohttp.ClientSession() as session:
        day_str = day.strftime('%Y%m%d')
        await max_conn_dce.acquire()
        async with session.post('http://www.dce.com.cn/PublicWeb/MainServlet', data={
                'action': 'Pu00011_result', 'Pu00011_Input.trade_date': day_str, 'Pu00011_Input.variety': 'all',
                'Pu00011_Input.trade_type': 0}) as response:
            rst = await response.text()
            max_conn_dce.release()
            soup = BeautifulSoup(rst, 'lxml')
            for tr in soup.select("tr")[2:-4]:
                inst_data = list(tr.stripped_strings)
                # error_data = inst_data
                """
[0'商品名称', 1'交割月份', 2'开盘价', 3'最高价', 4'最低价', 5'收盘价', 6'前结算价', 7'结算价', 8'涨跌', 9'涨跌1', 10'成交量', 11'持仓量', 12'持仓量变化', 13'成交额']
['豆一', '1609', '3,699', '3,705', '3,634', '3,661', '3,714', '3,668', '-53', '-46', '5,746', '5,104', '-976', '21,077.13']
                """
                if '小计' in inst_data[0]:
                    continue
                DailyBar.objects.update_or_create(
                    code=DCE_NAME_CODE[inst_data[0]] + inst_data[1],
                    exchange=ExchangeType.DCE, time=day, defaults={
                        'expire_date': inst_data[1],
                        'open': inst_data[2].replace(',', '') if inst_data[2] != '-' else
                        inst_data[5].replace(',', ''),
                        'high': inst_data[3].replace(',', '') if inst_data[3] != '-' else
                        inst_data[5].replace(',', ''),
                        'low': inst_data[4].replace(',', '') if inst_data[4] != '-' else
                        inst_data[5].replace(',', ''),
                        'close': inst_data[5].replace(',', ''),
                        'settlement': inst_data[7].replace(',', '') if inst_data[7] != '-' else
                        inst_data[6].replace(',', ''),
                        'volume': inst_data[10].replace(',', ''),
                        'open_interest': inst_data[11].replace(',', '')})


async def update_from_cffex(day: datetime.datetime):
    async with aiohttp.ClientSession() as session:
        await max_conn_cffex.acquire()
        async with session.get('http://www.cffex.com.cn/fzjy/mrhq/{}/index.xml'.format(
                day.strftime('%Y%m/%d'))) as response:
            rst = await response.text()
            max_conn_cffex.release()
            tree = ET.fromstring(rst)
            for inst_data in tree.getchildren():
                """
                <dailydata>
                <instrumentid>IC1609</instrumentid>
                <tradingday>20160824</tradingday>
                <openprice>6336.8</openprice>
                <highestprice>6364.4</highestprice>
                <lowestprice>6295.6</lowestprice>
                <closeprice>6314.2</closeprice>
                <openinterest>24703.0</openinterest>
                <presettlementprice>6296.6</presettlementprice>
                <settlementpriceIF>6317.6</settlementpriceIF>
                <settlementprice>6317.6</settlementprice>
                <volume>10619</volume>
                <turnover>1.3440868E10</turnover>
                <productid>IC</productid>
                <delta/>
                <segma/>
                <expiredate>20160919</expiredate>
                </dailydata>
                """
                # error_data = list(inst_data.itertext())
                DailyBar.objects.update_or_create(
                    code=inst_data.findtext('instrumentid').strip(),
                    exchange=ExchangeType.CFFEX, time=day, defaults={
                        'expire_date': inst_data.findtext('expiredate')[2:6],
                        'open': inst_data.findtext('openprice').replace(',', '') if inst_data.findtext(
                            'openprice') else inst_data.findtext('closeprice').replace(',', ''),
                        'high': inst_data.findtext('highestprice').replace(',', '') if inst_data.findtext(
                            'highestprice') else inst_data.findtext('closeprice').replace(',', ''),
                        'low': inst_data.findtext('lowestprice').replace(',', '') if inst_data.findtext(
                            'lowestprice') else inst_data.findtext('closeprice').replace(',', ''),
                        'close': inst_data.findtext('closeprice').replace(',', ''),
                        'settlement': inst_data.findtext('settlementprice').replace(',', '')
                        if inst_data.findtext('settlementprice') else
                        inst_data.findtext('presettlementprice').replace(',', ''),
                        'volume': inst_data.findtext('volume').replace(',', ''),
                        'open_interest': inst_data.findtext('openinterest').replace(',', '')})


def store_main_bar(bar: DailyBar):
    MainBar.objects.update_or_create(
        exchange=bar.exchange, product_code=re.findall('[A-Za-z]+', bar.code)[0], time=bar.time, defaults={
            'cur_code': bar.code,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'settlement': bar.settlement,
            'volume': bar.volume,
            'open_interest': bar.open_interest})


def handle_rollover(inst: Instrument, new_bar: DailyBar):
    """
    换月处理, 基差=新合约收盘价-旧合约收盘价, 从今日起之前的所有连续合约的OHLC加上基差
    """
    product_code = re.findall('[A-Za-z]+', new_bar.code)[0]
    old_bar = DailyBar.objects.filter(exchange=inst.exchange, code=inst.last_main, time=new_bar.time).first()
    main_bar = MainBar.objects.get(
        exchange=inst.exchange, product_code=product_code, time=new_bar.time)
    if old_bar is None:
        old_close = new_bar.close
    else:
        old_close = old_bar.close
    basis = new_bar.close - old_close
    main_bar.basis = basis
    basis = float(basis)
    main_bar.save(update_fields=['basis'])
    MainBar.objects.filter(exchange=inst.exchange, product_code=product_code, time__lte=new_bar.time).update(
        open=F('open') + basis, high=F('high') + basis,
        low=F('low') + basis, close=F('close') + basis, settlement=F('settlement') + basis)


def calc_main_inst(
        inst: Instrument,
        day: datetime.datetime = datetime.datetime.today().replace(tzinfo=pytz.FixedOffset(480))):
    """
    [["2016-07-18","2116.000","2212.000","2106.000","2146.000","34"],...]
    """
    updated = False
    if inst.main_code is not None:
        expire_date = calc_expire_date(inst.main_code, day)
    else:
        expire_date = day.strftime('%y%m')
    # 条件1: 成交量最大 & (成交量>1万 & 持仓量>1万 or 股指) = 主力合约
    if inst.exchange == ExchangeType.CFFEX:
        check_bar = DailyBar.objects.filter(
            exchange=inst.exchange, code__regex='^{}[0-9]+'.format(inst.product_code),
            expire_date__gte=expire_date,
            time=day).order_by('-volume').first()
    else:
        check_bar = DailyBar.objects.filter(
            exchange=inst.exchange, code__regex='^{}[0-9]+'.format(inst.product_code),
            expire_date__gte=expire_date,
            time=day, volume__gte=10000, open_interest__gte=10000).order_by('-volume').first()
    # 条件2: 不满足条件1但是连续3天成交量最大 = 主力合约
    if check_bar is None:
        check_bars = list(DailyBar.objects.raw(
            "SELECT a.* FROM panel_dailybar a INNER JOIN(SELECT time, max(volume) v, max(open_interest) i "
            "FROM panel_dailybar WHERE EXCHANGE=%s and CODE RLIKE %s GROUP BY time) b ON a.time = b.time "
            "AND a.volume = b.v AND a.open_interest = b.i "
            "where a.exchange=%s and code Rlike %s AND a.time <= %s ORDER BY a.time desc LIMIT 3",
            [inst.exchange, '^{}[0-9]+'.format(inst.product_code)] * 2 + [day.strftime('%y/%m/%d')]))
        if len(set(bar.code for bar in check_bars)) == 1:
            check_bar = check_bars[0]
        else:
            check_bar = None
    # 之前没有主力合约, 取当前成交量最大的作为主力
    if inst.main_code is None:
        if check_bar is None:
            check_bar = DailyBar.objects.filter(
                exchange=inst.exchange, code__regex='^{}[0-9]+'.format(inst.product_code),
                expire_date__gte=expire_date, time=day).order_by('-volume', '-open_interest', 'code').first()
        inst.main_code = check_bar.code
        inst.change_time = day
        inst.save(update_fields=['main_code', 'change_time'])
        store_main_bar(check_bar)
    # 主力合约发生变化, 做换月处理
    elif check_bar is not None and inst.main_code != check_bar.code and check_bar.code > inst.main_code:
        inst.last_main = inst.main_code
        inst.main_code = check_bar.code
        inst.change_time = day
        inst.save(update_fields=['last_main', 'main_code', 'change_time'])
        store_main_bar(check_bar)
        handle_rollover(inst, check_bar)
        updated = True
    else:
        bar = DailyBar.objects.filter(exchange=inst.exchange, code=inst.main_code, time=day).first()
        # 若当前主力合约当天成交量为0, 需要换下一个合约
        if bar is None or bar.volume == 0 or bar.open_interest == Decimal(0):
            check_bar = DailyBar.objects.filter(
                exchange=inst.exchange, code__regex='^{}[0-9]+'.format(inst.product_code),
                expire_date__gte=expire_date, time=day).order_by('-volume', '-open_interest').first()
            print('check_bar=', check_bar)
            if bar is None or bar.code != check_bar.code:
                inst.last_main = inst.main_code
                inst.main_code = check_bar.code
                inst.change_time = day
                inst.save(update_fields=['last_main', 'main_code', 'change_time'])
                store_main_bar(check_bar)
                handle_rollover(inst, check_bar)
                updated = True
            else:
                store_main_bar(bar)
        else:
            store_main_bar(bar)
    return inst.main_code, updated


def create_main(inst: Instrument):
    print('processing ', inst.product_code)
    for day in DailyBar.objects.all().order_by('time').values_list('time', flat=True).distinct():
        print(day, calc_main_inst(inst, datetime.datetime.combine(
            day, datetime.time.min.replace(tzinfo=pytz.FixedOffset(480)))))


def create_main_all():
    for inst in Instrument.objects.all():
        create_main(inst)
    print('all done!')


def is_auction_time(inst: Instrument, status: dict):
    if status['InstrumentStatus'] == ApiStruct.IS_AuctionOrdering:
        now = datetime.datetime.now().replace(tzinfo=pytz.FixedOffset(480))
        # 夜盘集合竞价时间是 20:55
        if inst.night_trade and now.hour == 20:
            return True
        # 日盘集合竞价时间是 8:55
        if not inst.night_trade and now.hour == 8:
            return True
    return False
