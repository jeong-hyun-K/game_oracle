from flask import Flask, render_template, request, jsonify
import requests
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import urllib.parse
import time
import concurrent.futures
import webbrowser
import os
import threading

from selenium import webdriver as sel_webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

app = Flask(__name__)

MAJOR_GAMES = ["Elden Ring", "Cyberpunk 2077", "GTA 5", "Hogwarts Legacy"]

# ==========================================
# 초고속 캐시 및 하트비트 세팅
# ==========================================
SEARCH_CACHE = {} 
CACHE_EXPIRE_SECONDS = 3600 

SERVER_START_TIME = time.time()
LAST_PING_TIME = time.time()

@app.route('/ping', methods=['POST'])
def ping():
    global LAST_PING_TIME
    LAST_PING_TIME = time.time() 
    return jsonify({"status": "alive"})

def watchdog():
    global LAST_PING_TIME, SERVER_START_TIME
    while True:
        time.sleep(2)
        current_time = time.time()
        if (current_time - SERVER_START_TIME > 30) and (current_time - LAST_PING_TIME > 10):
            print("\n[SYSTEM] 브라우저 창이 닫힌 것을 감지했습니다. 서버를 자동 종료합니다...")
            os._exit(0)

# ==========================================
# [설정] 세일 캘린더 & 수동 DB
# ==========================================
SALE_CALENDARS = {
    "Steam": [
        {"name": "봄 할인", "month": 3, "day": 14, "avg_discount": 50},
        {"name": "여름 대규모 세일", "month": 6, "day": 27, "avg_discount": 75},
        {"name": "가을(블프) 세일", "month": 11, "day": 21, "avg_discount": 60},
        {"name": "겨울 대규모 세일", "month": 12, "day": 21, "avg_discount": 75}
    ],
    "PlayStation": [
        {"name": "Days of Play (글로벌)", "month": 5, "day": 29, "avg_discount": 50},
        {"name": "여름 대할인 프로모션", "month": 7, "day": 17, "avg_discount": 60},
        {"name": "블랙 프라이데이", "month": 11, "day": 17, "avg_discount": 70},
        {"name": "홀리데이 세일", "month": 12, "day": 20, "avg_discount": 75}
    ],
    "Xbox": [
        {"name": "봄맞이 할인", "month": 4, "day": 5, "avg_discount": 50},
        {"name": "여름 궁극의 게임 세일", "month": 7, "day": 14, "avg_discount": 60},
        {"name": "블랙 프라이데이", "month": 11, "day": 20, "avg_discount": 70},
        {"name": "카운트다운 세일", "month": 12, "day": 17, "avg_discount": 75}
    ],
    "Nintendo": [
        {"name": "새해 맞이 세일", "month": 1, "day": 20, "avg_discount": 30},
        {"name": "봄맞이 세일", "month": 4, "day": 25, "avg_discount": 30},
        {"name": "여름 대할인", "month": 8, "day": 10, "avg_discount": 30},
        {"name": "겨울 홀리데이", "month": 12, "day": 15, "avg_discount": 30}
    ]
}

MANUAL_DB = [
    {'ko': '젤다', 'en': 'The Legend of Zelda: Tears of the Kingdom', 'steam_id': None, 'image': 'https://assets.nintendo.com/image/upload/ar_16:9,c_lpad,w_656/b_white/f_auto/q_auto/ncom/software/switch/70010000063714/desc/1b2c44d184a4411132646244455822365319888997a44a6f22849887754d550d'},
    {'ko': '사이버펑크', 'en': 'Cyberpunk 2077', 'steam_id': '1091500', 'image': 'https://image.api.playstation.com/vulcan/ap/rnd/202311/2811/1d713175788470a7243e8d9730598a3949f54b6099b25470.png'},
    {'ko': 'GTA', 'en': 'Grand Theft Auto V', 'steam_id': '271590', 'image': 'https://image.api.playstation.com/vulcan/ap/rnd/202202/2816/m3v6l243gH3v8b6Q1s4q4q4q.png'},
    {'ko': '호그와트', 'en': 'Hogwarts Legacy', 'steam_id': '990080', 'image': 'https://image.api.playstation.com/vulcan/ap/rnd/202208/0921/dR9KJAKDW2izPbptHQbh3rnj.png'},
    {'ko': '철권', 'en': 'TEKKEN 8', 'steam_id': '1778820', 'image': 'https://image.api.playstation.com/vulcan/ap/rnd/202308/0410/512968846183e840d2382760655d4090b63c7608151593c6.png'},
    {'ko': '엘든링', 'en': 'ELDEN RING', 'steam_id': '1245620', 'image': 'https://image.api.playstation.com/vulcan/ap/rnd/202110/2000/aGhopp3MHppi7kooZAcmWlGg.png'}
]

def get_names(user_query):
    korean_name = user_query
    english_name = user_query 
    for item in MANUAL_DB:
        if item['ko'] in user_query or item['en'].lower() in user_query.lower():
            return item['ko'], item['en'], item.get('image')
    try:
        url = "https://store.steampowered.com/api/storesearch/"
        res = requests.get(url, params={'term': user_query, 'l': 'english', 'cc': 'US'}).json()
        if res['total'] > 0: english_name = res['items'][0]['name']
    except: pass
    return korean_name, english_name, None

def clean_store_title(title):
    title = re.sub(r'\([^)]*(한국어|중국어|영어|일본어|태국어|러시아어|판|자)[^)]*\)', '', title)
    title = re.sub(r'(?i)ps4\s*&\s*ps5|ps4|ps5|xbox one|xbox series x\|s|windows', '', title)
    if title.startswith('G '): title = title[2:]
    return title.strip()

def calculate_similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def select_best_match(candidates, query_ko, query_en):
    if not candidates: return None
    
    scored_candidates = []
    dlc_keywords = ['phantom', 'liberty', 'dlc', 'pack', 'expansion', 'soundtrack', 'ost', 'pass', 'skin', 'token', 'bundle', 'deluxe', 'digital', 'coins', 'credit', 'add-on', '팩', '스킨', '사운드트랙', '에디션', '코인']
    version_keywords = ['ps4', 'ps5', 'xbox', 'switch', 'ver', '버전', 'standard', '일반판']
    is_cyberpunk = 'cyberpunk' in query_en.lower() or '사이버펑크' in query_ko.lower()

    core_words = set([w.lower() for w in query_en.split() if len(w) > 1] + [w for w in query_ko.split() if len(w) > 1])

    for item in candidates:
        name = item['name']
        price = item['price']
        lower_name = name.lower()
        
        score = 1000 
        if lower_name == query_en.lower() or lower_name == query_ko.lower(): score -= 2000 
        elif query_en.lower() in lower_name or query_ko.lower() in lower_name: score -= 500
            
        match_core = False
        for w in core_words:
            if w in lower_name: match_core = True
            
        if not match_core: score += 3000
        
        for k in dlc_keywords:
            if k in lower_name:
                if is_cyberpunk and ('ultimate' in lower_name or '얼티밋' in lower_name): continue
                if k in ['pack', '팩', 'skin', 'ost']: score += 1500 
                else: score += 100 
        
        for k in version_keywords:
            if k in lower_name: score -= 50 
        if price < 5000: score += 500

        scored_candidates.append((score, item))

    scored_candidates.sort(key=lambda x: x[0])
    best_score, best_item = scored_candidates[0]
    
    if best_score >= 800: return None
    return best_item

def get_steam_real(query_ko, query_en):
    for item in MANUAL_DB:
        if item['ko'] in query_ko or item['en'].lower() in query_en.lower():
            if item.get('steam_id'):
                app_id = item['steam_id']
                api_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc=kr"
                res = requests.get(api_url).json()
                if res[str(app_id)]['success']:
                    data = res[str(app_id)]['data']
                    price = 0
                    orig = 0
                    if 'price_overview' in data:
                        price = int(data['price_overview']['final'] / 100)
                        orig = int(data['price_overview']['initial'] / 100)
                    return {
                        'name': data['name'],
                        'price': price, 'original': orig, 'discount': 0,
                        'url': f"https://store.steampowered.com/app/{app_id}/",
                        'image': data.get('header_image', ''), 'platform': 'Steam'
                    }

    try:
        url = "https://store.steampowered.com/api/storesearch/"
        res = requests.get(url, params={'term': query_ko, 'l': 'koreana', 'cc': 'kr'}).json()
        candidates = []
        if res['total'] > 0:
            for item in res['items'][:10]:
                if 'price' in item:
                    candidates.append({
                        'name': item['name'],
                        'price': int(item['price']['final'] / 100),
                        'original': int(item['price'].get('initial', item['price']['final']) / 100),
                        'url': f"https://store.steampowered.com/app/{item['id']}",
                        'platform': 'Steam'
                    })
        return select_best_match(candidates, query_ko, query_en)
    except: return None

def search_platform(driver, platform_name, url_template, selector_logic, query_ko, query_en):
    candidates = []
    search_queries = [query_en]
    if query_ko != query_en: search_queries.append(query_ko)

    for q in search_queries:
        try:
            target_url = url_template.format(urllib.parse.quote(q))
            driver.get(target_url)
            
            # [핵심] 닌텐도도 엑스박스처럼 지연 로딩 회피(강제 스크롤) 적용!
            if platform_name in ["Xbox", "Nintendo"]:
                time.sleep(1) 
                driver.execute_script("window.scrollBy(0, 800);") 
                time.sleep(2.5) 
            else:
                time.sleep(1.5) 
                
            wait = WebDriverWait(driver, 5) 
            items = selector_logic(driver, wait, target_url)
            if items: candidates.extend(items)
        except Exception as e:
            continue

    unique = {f"{c['name']}_{c['price']}": c for c in candidates}.values()
    return select_best_match(list(unique), query_ko, query_en)

def ps_selector(driver, wait, base_url):
    found = []
    try:
        elements = driver.find_elements(By.TAG_NAME, "li")
        for el in elements:
            try:
                txt = el.get_attribute('innerText').strip()
                if not txt or "원" not in txt: continue
                
                lines = [l.strip() for l in txt.split('\n') if l.strip()]
                garbage_tags = ['ps4', 'ps5', '게임', '의상', '게임 번들', '추가 콘텐츠', '스페셜', '프리미엄', '체험판', '레벨', '에디션', '-85%', '-50%']
                
                title = lines[0]
                for line in lines:
                    if "원" in line or "%" in line: continue
                    if line.lower() in garbage_tags: continue
                    if len(line) > 3:
                        title = line
                        break
                clean_title = clean_store_title(title)
                
                match = re.findall(r'[0-9,]+원', txt)
                if match:
                    prices = [int(re.sub(r'[^0-9]', '', m)) for m in match]
                    price = min(prices)
                    original = max(prices) 
                    if price > 2000: 
                        found.append({'name': clean_title, 'price': price, 'original': original, 'url': base_url, 'platform': 'PlayStation'})
            except: continue
    except: pass
    return found

def xbox_selector(driver, wait, base_url):
    found = []
    try:
        cards = driver.find_elements(By.CSS_SELECTOR, "a[href*='/games/store/'], div[class*='ProductCard'], div.m-product-placement-item, div.card")
        for card in cards:
            txt = card.get_attribute('innerText').strip()
            if "원" not in txt and "₩" not in txt: continue
            
            lines = [l.strip() for l in txt.split('\n') if l.strip()]
            garbage_tags = ['콘솔', 'pc', '게임 패스', '게임패스', 'game pass', 'ea play', '할인', '포함', '예약']
            title = lines[0]
            for line in lines:
                if "원" in line or "₩" in line or "%" in line or "+" in line: continue
                if any(g in line.lower() for g in garbage_tags): continue
                if len(line) > 2 and not line.isdigit():
                    title = line
                    break
            clean_title = clean_store_title(title)
            
            match = re.findall(r'(?:₩\s*[0-9,]+|[0-9,]+\s*원)', txt)
            if match:
                prices = [int(re.sub(r'[^0-9]', '', m)) for m in match]
                price = min(prices)
                original = max(prices)
                if price > 2000: 
                    found.append({'name': clean_title, 'price': price, 'original': original, 'url': base_url, 'platform': 'Xbox'})
    except: pass
    return found

# ==========================================
# [핵심] Nintendo 전용 스캐너 (투망식으로 강화)
# ==========================================
def nintendo_selector(driver, wait, base_url):
    found = []
    try:
        # 최근 닌텐도 스토어의 가능한 카드 구조 싹 다 포함
        items = driver.find_elements(By.CSS_SELECTOR, ".product-item-info, .product-item, .category-product-item")
        for item in items[:8]:
            try:
                txt = item.get_attribute('innerText').strip()
                if not txt or ("원" not in txt and "₩" not in txt): continue
                
                # 제목 추출 시도 (실패하면 텍스트 첫 줄 사용)
                try:
                    title = item.find_element(By.CSS_SELECTOR, ".product-item-link, .product-item-name, .name").get_attribute('innerText').strip()
                except:
                    lines = [l.strip() for l in txt.split('\n') if l.strip()]
                    title = lines[0]

                clean_title = clean_store_title(title)
                
                # 가격 추출 (₩ 기호도 대응)
                match = re.findall(r'(?:₩\s*[0-9,]+|[0-9,]+\s*원)', txt)
                if match:
                    prices = [int(re.sub(r'[^0-9]', '', m)) for m in match]
                    price = min(prices)
                    original = max(prices)
                    found.append({'name': clean_title, 'price': price, 'original': original, 'url': base_url, 'platform': 'Nintendo'})
            except Exception as item_err: continue
    except Exception as e: print(f"Nintendo Selector Error: {e}")
    return found

def fetch_console_price(driver_path, platform_name, url_template, selector_logic, query_ko, query_en):
    options = Options()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = sel_webdriver.Chrome(service=Service(driver_path), options=options)
    try:
        return search_platform(driver, platform_name, url_template, selector_logic, query_ko, query_en)
    finally:
        driver.quit() 

def perform_scraping(user_query):
    name_ko, name_en, manual_img = get_names(user_query)
    
    steam_res = get_steam_real(name_ko, name_en)
    driver_path = ChromeDriverManager().install()

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_ps = executor.submit(fetch_console_price, driver_path, "PlayStation", "https://store.playstation.com/ko-kr/search/{}", ps_selector, name_ko, name_en)
        future_xbox = executor.submit(fetch_console_price, driver_path, "Xbox", "https://www.xbox.com/ko-kr/search?q={}", xbox_selector, name_ko, name_en)
        future_nin = executor.submit(fetch_console_price, driver_path, "Nintendo", "https://store.nintendo.co.kr/catalogsearch/result/?q={}", nintendo_selector, name_ko, name_en)
        
        ps_res = future_ps.result()
        xbox_res = future_xbox.result()
        nintendo_res = future_nin.result()

    base_img = "https://via.placeholder.com/300x150?text=No+Image"
    if manual_img: base_img = manual_img
    elif steam_res and steam_res.get('image'): base_img = steam_res['image']

    def fallback_url(platform, res, q_en, q_ko):
        if res and res.get('url') and res['url'] != "#": return res['url']
        enc_en = urllib.parse.quote(q_en)
        enc_ko = urllib.parse.quote(q_ko)
        if platform == 'PlayStation': return f"https://store.playstation.com/ko-kr/search/{enc_en}"
        if platform == 'Xbox': return f"https://www.xbox.com/ko-kr/search?q={enc_ko}"
        if platform == 'Nintendo': return f"https://store.nintendo.co.kr/catalogsearch/result/?q={enc_ko}"
        return '#'

    return {
        'name': name_en, 'image': base_img,
        'platforms': {
            'Steam': steam_res or {'price': 0, 'original': 0, 'url': '#'},
            'PlayStation': ps_res or {'price': 0, 'original': 0, 'url': fallback_url('PlayStation', ps_res, name_en, name_ko)},
            'Nintendo': nintendo_res or {'price': 0, 'original': 0, 'url': fallback_url('Nintendo', nintendo_res, name_en, name_ko)},
            'Xbox': xbox_res or {'price': 0, 'original': 0, 'url': fallback_url('Xbox', xbox_res, name_en, name_ko)}
        }
    }

def get_cached_prices(user_query):
    query_key = user_query.lower().strip()
    current_time = time.time()
    
    if query_key in SEARCH_CACHE:
        saved_data, saved_time = SEARCH_CACHE[query_key]
        if current_time - saved_time < CACHE_EXPIRE_SECONDS:
            return saved_data
            
    fresh_data = perform_scraping(user_query)
    if fresh_data:
        SEARCH_CACHE[query_key] = (fresh_data, current_time)
        
    return fresh_data

def predict_price(price, original, platform):
    if price == 0: 
        return {"recommendation": "데이터 없음", "probability": 0, "expected_price": 0, "sale_name": "-", "target_date": "-", "current_disc": 0, "original": 0}
    
    current_disc = 100 - int((price / original) * 100) if original > price else 0

    today = datetime.now()
    next_sale = None
    calendar = SALE_CALENDARS.get(platform, SALE_CALENDARS["Steam"])
    
    for sale in calendar:
        d = datetime(today.year, sale['month'], sale['day'])
        if d < today: d = datetime(today.year+1, sale['month'], sale['day'])
        if not next_sale or d < next_sale['date']:
            next_sale = {'name': sale['name'], 'date': d, 'disc': sale['avg_discount']}
    
    future_disc = next_sale['disc']
    exp_price = int(original * (1 - future_disc/100))
    target_date_str = next_sale['date'].strftime("%Y-%m-%d")

    if current_disc > 0:
        if current_disc >= future_disc:
            rec = f"🔥 현재 {current_disc}% 세일 중! (다음 예상 {future_disc}% 이상)"
            prob = 10
            exp_p = price
            s_name = "현재 역대급 세일!"
            t_date = "지금"
        else:
            rec = f"⚠️ 현재 {current_disc}% 세일 중이나 존버 추천 (다음 {future_disc}% 예상)"
            prob = 70
            exp_p = exp_price
            s_name = next_sale['name']
            t_date = target_date_str
    else:
        rec = f"존버 추천! {next_sale['name']} 대기"
        prob = 80
        exp_p = exp_price
        s_name = next_sale['name']
        t_date = target_date_str

    return {
        "recommendation": rec, 
        "probability": prob, 
        "expected_price": exp_p, 
        "sale_name": s_name, 
        "target_date": t_date,
        "original": original,
        "current_disc": current_disc
    }

def analyze(platforms):
    results = []
    for name, data in platforms.items():
        p = data.get('price', 0)
        orig = data.get('original', p)
        found_name = data.get('name', '상품을 찾을 수 없음') 
        
        if p == 0:
            pred_dummy = {'recommendation': '-', 'target_date': '-', 'probability': 0, 'expected_price': 0, 'sale_name': '-', 'current_disc': 0, 'original': 0}
            results.append({'name': name, 'found_name': '판매처 없음', 'price': 0, 'advice': "데이터 없음", 'color': '#555', 'pred': pred_dummy, 'url': '#'})
            continue
        
        pred = predict_price(p, orig, name)
        results.append({
            'name': name, 
            'found_name': found_name, 
            'price': p, 
            'advice': pred['recommendation'], 
            'color': '#00C851' if pred['probability'] < 50 else '#ff4444', 
            'pred': pred, 
            'url': data.get('url', '#')
        })
    return results

@app.route('/suggest')
def suggest():
    q = request.args.get('q', '').lower()
    if len(q) < 2: return jsonify([])
    suggestions = []
    for item in MANUAL_DB:
        if any(k in q for k in [item['ko'], item['en'].lower()]):
            suggestions.append({'name': item['en'], 'price': 0, 'image': item['image'], 'source': 'Popular'})
    try:
        url = "https://store.steampowered.com/api/storesearch/"
        res = requests.get(url, params={'term': q, 'l': 'koreana', 'cc': 'kr'}).json()
        if res['total'] > 0:
            for i in res['items'][:4]:
                if not any(s['name'] == i['name'] for s in suggestions):
                    suggestions.append({'name': i['name'], 'price': int(i['price']['final']/100) if 'price' in i else 0, 'image': i.get('tiny_image', ''), 'source': 'Steam'})
    except: pass
    return jsonify(suggestions)

@app.route('/', methods=['GET', 'POST'])
def home():
    global LAST_PING_TIME, SERVER_START_TIME
    LAST_PING_TIME = time.time()
    
    search_result = None
    dashboard = []
    if request.method == 'GET':
        pass

    if request.method == 'POST':
        query = request.form.get('game_name')
        data = get_cached_prices(query) 
        if data:
            search_result = {'info': data, 'analysis': analyze(data['platforms'])}

    return render_template('index.html', dashboard=dashboard, search_result=search_result)

def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000/")

if __name__ == '__main__':
    threading.Timer(1.5, open_browser).start()
    threading.Thread(target=watchdog, daemon=True).start()
    app.run(debug=False, use_reloader=False)