#!/usr/bin/env python3
"""Local, deterministic party bar planner. Python 3.10+, standard library only."""
import argparse
import csv
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP, getcontext
import html
import json
from pathlib import Path
import re
import sys

getcontext().prec = 80

UNITS = {'ml': ('volume', Decimal(1)), 'L': ('volume', Decimal(1000)),
         'g': ('mass', Decimal(1)), 'kg': ('mass', Decimal(1000)), '个': ('count', Decimal(1))}
BASE = {'volume': 'ml', 'mass': 'g', 'count': '个'}
class InputError(ValueError):
    pass

def require(ok, message):
    if not ok:
        raise InputError(message)

def number(value, where, positive=False):
    require(not isinstance(value, bool), f'{where}: 必须是有限非负数字')
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InputError(f'{where}: 必须是有限非负数字')
    require(n.is_finite() and n >= 0 and (not positive or n > 0), f'{where}: 数值必须{"大于零" if positive else "非负"}')
    require(len(n.as_tuple().digits) <= 16 and abs(n.as_tuple().exponent) <= 6 and n <= Decimal('1000000000'), f'{where}: 超出精度/范围（最多16位、6位小数、上限10亿）')
    return n

def integer(value, where, positive=False):
    n = number(value, where, positive)
    require(n == n.to_integral_value(), f'{where}: 必须是整数')
    return int(n)

def string(value, where):
    require(isinstance(value, str) and value.strip() != '' and len(value) <= 2000, f'{where}: 必须是非空文本（最多2000字符）')
    return value

def quantity(value, unit, where):
    require(unit in UNITS, f'{where}: 非法单位 {unit!r}；仅支持 ml/L/g/kg/个，不自动转换质量与体积')
    dim, factor = UNITS[unit]
    return number(value, where) * factor, dim

def fmt(n):
    if n is None:
        return '待确认'
    return format(n, 'f').rstrip('0').rstrip('.') if '.' in format(n, 'f') else str(n)

def money(n):
    return format(n.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")

def md(s):
    return html.escape(str(s)).replace('\\', '\\\\').replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')

def calculate(data):
    require(isinstance(data, dict), '根节点必须是 JSON 对象')
    for key in ('event', 'currency', 'ingredients', 'recipes', 'plan'):
        require(key in data, '请确认饮用计划：人数之外还需每款杯数或总杯数与比例' if key == 'plan' else f'缺少字段 {key}')
    string(data['event'], 'event')
    currency = data['currency']
    require(isinstance(currency, str) and re.fullmatch(r'[A-Z]{3}', currency), 'currency: 使用三字母单币种代码，如 HKD/CNY')
    catalog = data['ingredients']
    require(isinstance(catalog, dict) and catalog, 'ingredients: 必须是非空对象')
    aliases = data.get('aliases', {})
    require(isinstance(aliases, dict), 'aliases: 必须是对象')
    for alias, target in aliases.items():
        string(alias, 'alias')
        require(alias not in catalog and target in catalog, f'aliases.{alias}: 别名不能覆盖正式 ID，目标必须是正式 ID（不支持链式别名）')
    normalized = {}
    for key, item in catalog.items():
        string(key, 'ingredient id')
        require(isinstance(item, dict), f'{key}: 必须是对象')
        string(item.get('name'), f'{key}.name')
        pack, dim = quantity(item.get('package_size'), item.get('unit'), f'{key}.package_size')
        require(pack > 0, f'{key}: 包装量必须大于零')
        price = item.get('price')
        if price is not None:
            price = number(price, f'{key}.price')
        require(item.get('currency', currency) == currency, f'{key}: 混合币种，不能自动换汇')
        require(isinstance(item.get('serving_only', False), bool), f'{key}.serving_only: 必须是布尔值')
        normalized[key] = dict(name=item['name'], pack=pack, dim=dim, price=price, stock=Decimal(0), serving_only=item.get('serving_only', False))
    stock_seen = set()
    inventory = data.get('inventory', [])
    require(isinstance(inventory, list), 'inventory: 必须是数组')
    def resolve(key):
        require(isinstance(key, str), 'ingredient: 必须是文本 ID')
        key = aliases.get(key, key)
        require(key in normalized, f'未知原料 {key!r}；请添加正式 ID 或显式 aliases 映射')
        return key
    for item in inventory:
        key = resolve(item.get('ingredient'))
        require(key not in stock_seen, f'inventory: 重复库存 {key}，请先合并，避免重复抵扣')
        stock_seen.add(key)
        q, dim = quantity(item.get('quantity'), item.get('unit'), f'inventory.{key}')
        require(dim == normalized[key]['dim'], f'inventory.{key}: 质量/体积/个数维度不符')
        normalized[key]['stock'] = q
    recipes = data['recipes']
    require(isinstance(recipes, list) and recipes, 'recipes: 必须是非空数组')
    recipe_map = {}
    warnings = []
    for recipe in recipes:
        key = string(recipe.get('id'), 'recipe.id')
        require(key not in recipe_map, f'重复配方 ID {key}')
        for field in ('name', 'method', 'garnish', 'glass'):
            string(recipe.get(field), f'{key}.{field}')
        require(isinstance(recipe.get('alcoholic'), bool), f'{key}.alcoholic: 必须明确 true/false')
        lines = recipe.get('ingredients')
        require(isinstance(lines, list) and lines, f'{key}.ingredients: 必须非空')
        parsed = []
        for line in lines:
            ing = resolve(line.get('ingredient'))
            q, dim = quantity(line.get('quantity'), line.get('unit'), f'{key}.{ing}')
            require(q > 0 and dim == normalized[ing]['dim'], f'{key}.{ing}: 用量必须大于零且单位维度匹配；无密度不换算')
            stage = line.get('stage', 'uncertain')
            require(stage in ('premix', 'serve', 'uncertain'), f'{key}.{ing}: stage 应为 premix/serve/uncertain')
            role = line.get('role', 'base')
            require(role in ('base', 'garnish'), f'{key}.{ing}: role 应为 base/garnish')
            require(not ((normalized[ing]['serving_only'] or role == 'garnish') and stage == 'premix'), f'{key}.{ing}: 汽水/冰/鲜切装饰等 serving_only 原料及装饰不能预混')
            parsed.append(dict(ingredient=ing, quantity=q, stage=stage, role=role))
        recipe_map[key] = dict(recipe, parsed=parsed)
    plan = data['plan']
    require(isinstance(plan, dict), 'plan: 必须是对象')
    guests = integer(plan.get('guests'), 'plan.guests', True)
    counts = {key: 0 for key in recipe_map}
    mode = plan.get('mode')
    if mode == 'per_guest':
        servings = plan.get('servings_per_guest')
        require(isinstance(servings, dict) and servings, '请确认饮用计划：每位客人各喝哪些款、各几杯？')
        for key, n in servings.items():
            require(key in counts, f'plan: 未知配方 {key}')
            counts[key] = guests * integer(n, f'plan.{key}')
    elif mode == 'total_ratio':
        total = integer(plan.get('total_cups'), 'plan.total_cups', True)
        weights = plan.get('weights')
        require(isinstance(weights, dict) and weights, 'plan.weights: 请提供各款比例')
        for key in weights:
            require(key in counts, f'plan: 未知配方 {key}')
        weights = {key: number(n, f'weights.{key}') for key, n in weights.items()}
        # Integer micro-units preserve exact remainder ordering even for close ratios.
        weights = {key: int(weight * 1000000) for key, weight in weights.items()}
        denominator = sum(weights.values())
        require(denominator > 0, 'weights: 比例总和必须大于零')
        remainders = {}
        for key, weight in weights.items():
            counts[key], remainders[key] = divmod(total * weight, denominator)
        order = sorted(weights, key=lambda key: (-remainders[key], key))
        for key in order[:total - sum(counts.values())]:
            counts[key] += 1
    else:
        raise InputError('请确认饮用计划：使用 per_guest（每人各款杯数）或 total_ratio（总杯数与比例），人数不能代替杯数')
    require(sum(counts.values()) > 0, '饮用计划必须至少包含一杯')
    config = data.get('config', {})
    require(isinstance(config, dict), 'config: 必须是对象')
    reserve = number(config.get('reserve_rate', 0), 'reserve_rate')
    garnish_loss = number(config.get('garnish_loss_rate', 0), 'garnish_loss_rate')
    require(reserve <= 1 and garnish_loss <= 1, '余量/损耗率必须在 0 到 1 之间')
    demand = {key: Decimal(0) for key in normalized}
    loss = dict(demand)
    for key, recipe in recipe_map.items():
        if not counts[key]:
            continue
        for line in recipe['parsed']:
            q = line['quantity'] * counts[key]
            demand[line['ingredient']] += q
            if line['role'] == 'garnish':
                loss[line['ingredient']] += q * garnish_loss
            if line['stage'] == 'uncertain':
                warnings.append(f'{recipe["name"]} / {normalized[line["ingredient"]]["name"]}：是否可预混待确认，暂按出杯时添加。')
    ice = config.get('ice', {})
    require(isinstance(ice, dict), 'ice: 必须是对象')
    ice_totals = {}
    for role in ('mixing', 'serving'):
        rule = ice.get(role)
        if rule is None:
            warnings.append(f'{role} 用冰规则待确认；采购总量不含此项。')
            continue
        ing = resolve(rule.get('ingredient'))
        q, dim = quantity(rule.get('quantity_per_cup'), rule.get('unit'), f'ice.{role}')
        require(dim == normalized[ing]['dim'] and dim == 'mass', '用冰规则须以 g/kg 指定，且原料维度匹配')
        selected = rule.get('recipes')
        require(isinstance(selected, list) and len(set(selected)) == len(selected) and all(k in counts for k in selected), 'ice.recipes: 必须显式列出适用配方 ID，无重复')
        require(not any(counts[k] and any(line['ingredient'] == ing for line in recipe_map[k]['parsed']) for k in selected), f'ice.{role}: 适用配方已列同一冰原料，请只保留一种计量来源，避免重复计算')
        amount = q * sum(counts[k] for k in selected)
        demand[ing] += amount
        ice_totals[role] = dict(ingredient=ing, quantity=amount, per_cup=q, recipes=selected)
    rows = []
    for key, item in normalized.items():
        base = demand[key]
        if not base:
            continue
        extra = base * reserve
        target = base + extra + loss[key]
        # Counts may be fractional in recipe (half a lime), but procurement is whole pieces.
        procurement = target.to_integral_value(rounding=ROUND_CEILING) if item['dim'] == 'count' else target
        shortfall = max(Decimal(0), procurement - item['stock'])
        packages = int((shortfall / item['pack']).to_integral_value(rounding=ROUND_CEILING))
        cost = None if item['price'] is None else base / item['pack'] * item['price']
        spend = None if item['price'] is None and packages else Decimal(packages) * (item['price'] or 0)
        rows.append(dict(id=key, **item, unit=BASE[item['dim']], base=base, reserve=extra, loss=loss[key],
                         target=target, procurement=procurement, packages=packages,
                         leftover=item['stock'] + packages * item['pack'] - base,
                         after_target=item['stock'] + packages * item['pack'] - target,
                         consumption_cost=cost, purchase_cost=spend))
    def summary(field):
        return dict(known=sum((row[field] for row in rows if row[field] is not None), Decimal(0)),
                    complete=all(row[field] is not None for row in rows))
    return dict(event=data['event'], currency=currency, guests=guests, counts=counts, recipes=recipe_map,
                rows=rows, warnings=list(dict.fromkeys(warnings)), ice=ice_totals,
                consumption=summary('consumption_cost'), purchase=summary('purchase_cost'))

CSS = '''@page { size: A4; margin: 15mm; }
* { box-sizing: border-box; } body { margin:0; color:#153f33; background:#eeeade; font-family:"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.5; }
main { max-width:180mm; margin:24px auto; padding:12mm; background:#fffdf4; border-top:6px solid #163e32; }
header { border-bottom:1px solid #ad9156; padding-bottom:5mm; } h1 { font-size:28px; margin:0 0 3mm; overflow-wrap:anywhere; } .eyebrow { color:#806a37; letter-spacing:2px; font-size:11px; } article { padding:6mm 0; border-bottom:1px solid #d7cfb9; break-inside:avoid; page-break-inside:avoid; } h2 { font-size:20px; margin:0 0 2mm; overflow-wrap:anywhere; } p { margin:2mm 0; overflow-wrap:anywhere; } .tag,footer { font-size:11px; color:#586b5e; } .ingredients { font-size:13px; } footer { padding-top:5mm; } @media print { body { background:white; } main { margin:0; padding:0; max-width:none; border-top:4px solid #163e32; } }'''

def bar_guide(result):
    """One operational handoff: single drinks, totals, prep and service."""
    names = {row['id']: row for row in result['rows']}
    stages = {'premix': '可预混（按输入确认）', 'serve': '出杯时添加',
              'uncertain': '是否可预混待确认；暂按出杯时添加'}
    ice_names = {'mixing': '摇和/搅拌用冰', 'serving': '出杯用冰'}
    active = [(key, recipe, result['counts'][key]) for key, recipe in result['recipes'].items()
              if result['counts'][key]]
    lines = [f'# {md(result["event"])} · 备料与出酒操作手册', '',
             f'人数：{result["guests"]} 人；计划出酒：{sum(result["counts"].values())} 杯。', '',
             '演示配方未经实际试饮。以下用量来自输入配方；余量和损耗只用于备料，不加入单杯配方。', '',
             '## 一、完整单杯配方', '']
    for key, recipe, cups in active:
        lines += [f'### {md(recipe["name"])}', '',
                  f'{"含酒精" if recipe["alcoholic"] else "无酒精（按输入声明）"}；计划 {cups} 杯；杯型：{md(recipe["glass"])}。', '',
                  '| 原料 | 每杯用量 | 添加阶段 |', '|---|---|---|']
        for item in recipe['parsed']:
            row = names[item['ingredient']]
            lines.append(f'| {md(row["name"])} | {fmt(item["quantity"])} {row["unit"]} | {stages[item["stage"]]} |')
        lines += ['', f'做法：{md(recipe["method"])}', '', f'装饰：{md(recipe["garnish"])}', '']
        for role, rule in result['ice'].items():
            if key in rule['recipes']:
                lines += [f'- {ice_names[role]}：每杯 {fmt(rule["per_cup"])} g。']
        lines += ['']
    lines += ['## 二、总共需要的材料', '',
              '基础需求是计划杯数的实际配方用量（含已确认冰量）；备料目标另加安全余量和装饰损耗。库存只抵扣采购；包装向上取整后的多余材料不需要全部倒入饮品。', '',
              '| 材料 | 基础需求 | 安全余量 | 装饰损耗 | 总备料目标 | 已有库存 | 需买整包装 |',
              '|---|---|---|---|---|---|---|']
    for row in result['rows']:
        unit = row['unit']
        lines.append(f'| {md(row["name"])} | {fmt(row["base"])} {unit} | {fmt(row["reserve"])} {unit} | {fmt(row["loss"])} {unit} | {fmt(row["target"])} {unit} | {fmt(row["stock"])} {unit} | {row["packages"]} × {fmt(row["pack"])} {unit} |')
    lines += ['', '用冰用途拆分（基础量，安全余量已在总表单列）：', '']
    for role, rule in result['ice'].items():
        lines.append(f'- {ice_names[role]}：共 {fmt(rule["quantity"])} g。')
    lines += ['', '## 三、提前准备工作', '',
              '1. 先解决末尾待确认项；核对原料、库存和包装，按总材料表补齐。',
              '2. 清洁量具、容器与出酒工具，备好各款杯型；分别标记含酒精和无酒精原料/容器，核对所用原料。',
              '3. 按下表逐款称量。仅混合标记为可预混的原料，每款独立装容器并标注名称、计划杯数和配比；预混条件由输入确认，不承诺保存期限。',
              '4. 汽水保持独立；冰按摇和/搅拌和出杯用途分别备好；装饰材料先备齐，鲜切装饰出杯时处理。不把余量与损耗混入预混批次。', '']
    for key, recipe, cups in active:
        lines += [f'### {md(recipe["name"])} · {cups} 杯备料', '',
                  '| 原料 | 本款批量基础用量 | 准备方式 |', '|---|---|---|']
        for item in recipe['parsed']:
            row = names[item['ingredient']]
            lines.append(f'| {md(row["name"])} | {fmt(item["quantity"] * cups)} {row["unit"]} | {stages[item["stage"]]} |')
        for role, rule in result['ice'].items():
            if key in rule['recipes']:
                lines.append(f'| {ice_names[role]} | {fmt(rule["per_cup"] * cups)} g | 独立备料，不预混 |')
        lines += ['']
    lines += ['## 四、当晚出酒流程', '',
              '接单 → 确认酒款及是否无酒精 → 按下列对应流程逐杯制作 → 递杯并记录已出杯数。余量只用于实际需要时补料，不提高单杯用量。', '']
    for key, recipe, cups in active:
        lines += [f'### {md(recipe["name"])} · 按此流程出 1 杯', '',
                  f'1. 确认本款计划 {cups} 杯、当前剩余杯数，准备 {md(recipe["glass"])}。']
        premix = [item for item in recipe['parsed'] if item['stage'] == 'premix']
        if premix:
            detail = '；'.join(f'{md(names[item["ingredient"]]["name"])} {fmt(item["quantity"])} {names[item["ingredient"]]["unit"]}' for item in premix)
            lines.append(f'2. 取一杯份基础料：{detail}。如已按上表预混整批，取整批的 1/{cups}，不要取整批一次出杯；未预混则逐项量取。')
        else:
            lines.append('2. 按单杯配方表逐项量取；没有已确认的预混料。')
        late = [item for item in recipe['parsed'] if item['stage'] != 'premix']
        detail = '；'.join(f'{md(names[item["ingredient"]]["name"])} {fmt(item["quantity"])} {names[item["ingredient"]]["unit"]}' for item in late)
        lines.append(f'3. 单独备妥出杯时添加的材料：{detail or "无额外列明材料"}。按原做法中的顺序添加，汽水、冰和鲜切装饰不加入预混容器。')
        for role, rule in result['ice'].items():
            if key in rule['recipes']:
                lines.append(f'   - 本杯{ice_names[role]}：{fmt(rule["per_cup"])} g。')
        lines += [f'4. 执行原配方做法：{md(recipe["method"])}',
                  f'5. 核对装饰：{md(recipe["garnish"])}；核对酒款后递杯，已出杯计数加一。', '']
    lines += ['## 五、待确认项与交付文件', '']
    lines += [f'- {md(warning)}' for warning in result['warnings']] or ['- 当前输入没有脚本识别的待确认项；真实原料、预混适用性仍需使用者核对。']
    lines += ['', '采购剩余量和成本见 shopping.md / costs.csv；对客打印酒单见 menu.html。本手册只包含本次计划中实际制作的饮品。', '']
    return '\n'.join(lines)


def write_outputs(result, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = result['rows']
    (out/'bar-guide.md').write_text(bar_guide(result), encoding='utf-8')
    # All filenames are fixed; untrusted event/recipe names are never paths.
    def csv_write(filename, headers, values):
        with (out / filename).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in values:
                # Prevent spreadsheet formula injection while retaining Decimal values as numeric cells.
                writer.writerow([("'" + x if isinstance(x, str) and x.lstrip().startswith(('=', '+', '-', '@')) else x) for x in row])
    headers = ['原料ID','原料','单位','基础需求','安全余量','装饰损耗','备料目标','采购取整需求','库存','包装规格','购买包数','基础使用后剩余','目标备料后剩余']
    values = [[r['id'],r['name'],r['unit'], *[fmt(r[k]) for k in ('base','reserve','loss','target','procurement','stock','pack','packages','leftover','after_target')]] for r in rows]
    csv_write('shopping.csv', headers, values)
    csv_write('costs.csv', ['原料ID','币种','包装价格','实际耗用成本','整包装采购支出','价格状态'],
              [[r['id'],result['currency'], '' if r['price'] is None else money(r['price']),
                '' if r['consumption_cost'] is None else money(r['consumption_cost']),
                '' if r['purchase_cost'] is None else money(r['purchase_cost']),
                '价格待确认' if r['price'] is None else '已知'] for r in rows])
    shopping = [f'# {md(result["event"])} · 采购备料', f'人数：{result["guests"]}；总杯数：{sum(result["counts"].values())}。',
                '比例分杯：最大余数法，先向下取整，再按余数降序补杯；同余数按配方 ID 字典序。',
                '基础需求不含余量和装饰损耗。剩余量含既有库存；目标备料后剩余假设余量与损耗均已领用。',
                '| ' + ' | '.join(headers[1:]) + ' |', '|' + '---|' * (len(headers)-1)]
    shopping += ['| ' + ' | '.join(md(v) for v in row[1:]) + ' |' for row in values]
    shopping += ['', '## 成本摘要', '耗用成本按基础用量和包装单价分摊，含用冰；余量与装饰损耗不计入基础耗用成本。库存仍按相同单价估值。金额以 Decimal 80 位精度计算，展示时四舍五入到分；总计由未舍入明细汇总，可能与展示行相加有分位差。']
    for field, label in [('consumption','配方实际耗用成本'),('purchase','本次整包装采购支出')]:
        s = result[field]
        shopping.append(f'{label}：{money(s["known"])} {result["currency"]}（{"完整合计" if s["complete"] else "仅已知小计；完整总价待确认"}）。')
    shopping += ['', '## 用冰与待确认项']
    shopping += [f'- {role}：{fmt(item["quantity"])} {next(r["unit"] for r in rows if r["id"] == item["ingredient"])}' for role,item in result['ice'].items()]
    shopping += [f'- {md(w)}' for w in result['warnings']]
    (out/'shopping.md').write_text('\n\n'.join(shopping[:4])+'\n\n'+'\n'.join(shopping[4:])+'\n', encoding='utf-8')
    batch = ['# 批量配方与准备步骤', '演示配方未经实际试饮。预混分类由输入提供；不承诺保存期限。', '1. 核对库存、价格及待确认项，按采购清单备料。\n2. 清洁工具；按每款下表分别称量。\n3. 仅预混明确标记的原料；汽水、冰、鲜切装饰在出杯时添加。\n4. 按原做法出杯。冰分别按 mixing / serving 规则备料，不加入预混。']
    cards = []
    names = {r['id']:r for r in rows}
    for key, recipe in result['recipes'].items():
        cups = result['counts'][key]
        if not cups:
            continue
        batch += [f'## {md(recipe["name"])} · {cups} 杯', '| 原料 | 单杯 | 批量基础用量 | 阶段 |','|---|---|---|---|']
        menu_lines = []
        for line in recipe['parsed']:
            r = names[line['ingredient']]
            single = f'{fmt(line["quantity"])} {r["unit"]}'
            batch.append(f'| {md(r["name"])} | {single} | {fmt(line["quantity"]*cups)} {r["unit"]} | {line["stage"]} |')
            menu_lines.append(r['name'])
        for role, rule in result['ice'].items():
            if key in rule['recipes']:
                batch.append(f'用冰 {role}：单杯 {fmt(rule["per_cup"])} g；本款 {fmt(rule["per_cup"] * cups)} g（独立备料，不预混）。')
        batch += [f'做法：{md(recipe["method"])}',f'装饰：{md(recipe["garnish"])}；杯型：{md(recipe["glass"])}']
        esc = html.escape
        cards.append(f'<article><h2>{esc(recipe["name"])}</h2><p class="tag">{"含酒精 / Alcoholic" if recipe["alcoholic"] else "无酒精 / Alcohol-free"} · {esc(recipe["glass"])}</p><p class="ingredients">{esc(" · ".join(menu_lines))}</p><p>{esc(recipe["method"])}</p><p class="tag">装饰 / Garnish: {esc(recipe["garnish"])}</p></article>')
    batch_text = re.sub(r'(?m)^(\|.*)\n\n(?=\|)', r'\1\n', '\n\n'.join(batch))
    (out/'batches.md').write_text(batch_text+'\n', encoding='utf-8')
    (out/'menu.html').write_text(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(result["event"])}</title><style>{CSS}</style></head><body><main><header><p class="eyebrow">PARTY BAR · DRINKS MENU</p><h1>{html.escape(result["event"])}</h1></header>{"".join(cards)}<footer>虚构活动 · 演示配方未经实际试饮 / Untested demonstration recipes</footer></main></body></html>', encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(dict(guests=result['guests'], counts=result['counts'], total_cups=sum(result['counts'].values()), warnings=result['warnings']),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def load(path):
    def unique(pairs):
        d = {}
        for key,value in pairs:
            require(key not in d, f'JSON 重复字段 {key}')
            d[key] = value
        return d
    with Path(path).open(encoding='utf-8') as f:
        return json.load(f, parse_float=Decimal, object_pairs_hook=unique)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='JSON 配方、库存及计划')
    parser.add_argument('--out', required=True, help='本地输出目录（覆盖其中同名生成文件）')
    args = parser.parse_args()
    try:
        result = calculate(load(args.input))
        write_outputs(result, args.out)
    except (InputError, OSError, ValueError, TypeError, AttributeError, KeyError) as e:
        print(f'输入/输出错误：{e}', file=sys.stderr)
        return 2
    print(f'已生成 {args.out}：{result["guests"]} 人 / {sum(result["counts"].values())} 杯')
    return 0
if __name__ == '__main__':
    sys.exit(main())
