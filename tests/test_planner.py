import copy
import csv
from decimal import Decimal as D
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('planner',ROOT/'party-bar-planner/scripts/planner.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class PlannerTests(unittest.TestCase):
    def setUp(self):self.data=p.load(ROOT/'examples/party-6.json')
    def result(self):return p.calculate(self.data)
    def rows(self):return {r['id']:r for r in self.result()['rows']}
    def bad(self,part=''):
        with self.assertRaisesRegex(p.InputError,part):self.result()
    def test_independent_hand_calculation(self):
        # Independent expected numbers derived in examples/HAND-CHECK.md.
        expected={'gin':('160','16','0','1','640','32','140'),
                  'rum':('160','16','0','1','540','28.8','126'),
                  'lime-juice':('240','24','0','1','110','24','25'),
                  'syrup':('120','12','0','0','130','9.6','0'),
                  'soda':('880','88','0','3','110','16','18'),
                  'lime':('6','0.6','1.2','8','2','24','32'),
                  'ice':('2240','224','0','2','1760','22.4','40')}
        for k,values in expected.items():
            r=self.rows()[k]
            self.assertEqual(tuple(r[f] for f in ['base','reserve','loss','packages','leftover','consumption_cost','purchase_cost']),tuple(D(v) for v in values))
        self.assertEqual(self.result()['consumption']['known'],D('156.8'))
        self.assertEqual(self.result()['purchase']['known'],D('381'))
    def test_units(self):
        self.assertEqual(p.quantity('0.7','L','x'),(D('700'),'volume'))
        self.assertEqual(p.quantity('0.25','kg','x'),(D('250'),'mass'))
        self.assertEqual(p.quantity('0.5','个','x'),(D('.5'),'count'))
    def test_duplicate_lines_and_aliases(self):
        lines=self.data['recipes'][0]['ingredients'];lines[1]['quantity']='10'
        lines.append(dict(ingredient='lime-juice',quantity='10',unit='ml',stage='premix'))
        self.assertEqual(self.rows()['lime-juice']['base'],D(240))
    def test_no_fuzzy_merge(self):
        self.data['recipes'][0]['ingredients'][1]['ingredient']='青檸汁'
        self.bad('未知原料')
    def test_distinct_coconuts(self):
        for k in ['椰奶','椰乳','椰子水']:
            self.data['ingredients'][k]=dict(name=k,package_size=1000,unit='ml',price=10)
            self.data['recipes'][0]['ingredients'].append(dict(ingredient=k,quantity=1,unit='ml'))
        rows=self.rows()
        self.assertEqual([rows[k]['base'] for k in ['椰奶','椰乳','椰子水']],[D(4)]*3)
    def test_distinct_spirits(self):
        self.assertEqual(self.rows()['gin']['base'],D(160))
        self.assertEqual(self.rows()['rum']['base'],D(160))
    def test_inventory_sufficient_and_short(self):
        self.assertEqual(self.rows()['syrup']['packages'],0)
        self.assertEqual(self.rows()['gin']['packages'],1)
        self.assertGreater(self.rows()['syrup']['consumption_cost'],0)
    def test_duplicate_inventory_rejected(self):
        self.data['inventory'].append(copy.deepcopy(self.data['inventory'][0]));self.bad('重复库存')
    def test_package_ceiling(self):
        self.data['ingredients']['gin']['package_size']='50'
        self.assertEqual(self.rows()['gin']['packages'],2) # (176-100)/50 => 2
    def test_decimal_precision(self):
        self.data['recipes'][0]['ingredients'][0]['quantity']='0.1'
        self.assertEqual(self.rows()['gin']['base'],D('0.4'))
        self.assertEqual(self.rows()['gin']['reserve'],D('0.04'))
    def test_ratio_tie_and_total(self):
        self.data['plan']['total_cups']=8
        self.assertEqual(self.result()['counts'],dict(forest=3,gold=3,zero=2))
        self.data['plan']['total_cups']=20
        self.assertEqual(self.result()['counts'],dict(forest=7,gold=7,zero=6))
    def test_per_guest(self):
        self.data['plan']=dict(mode='per_guest',guests=6,servings_per_guest=dict(zero=2))
        self.assertEqual(self.result()['counts'],dict(forest=0,gold=0,zero=12))
        self.assertNotIn('gin',self.rows())
    def test_missing_price(self):
        self.data['ingredients']['gin']['price']=None
        r=self.result();self.assertFalse(r['consumption']['complete']);self.assertFalse(r['purchase']['complete'])
        with tempfile.TemporaryDirectory() as t:
            p.write_outputs(r,t)
            with open(Path(t)/'costs.csv',encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            self.assertEqual(rows[0]['实际耗用成本'],'');self.assertEqual(rows[0]['整包装采购支出'],'')
    def test_missing_price_no_purchase(self):
        self.data['ingredients']['syrup']['price']=None
        self.assertTrue(self.result()['purchase']['complete'])
        self.assertFalse(self.result()['consumption']['complete'])
    def test_mixed_currency(self):
        self.data['ingredients']['gin']['currency']='CNY';self.bad('混合币种')
    def test_invalid_numbers_units(self):
        for val in [0,-1,'0.1',True,'NaN','Infinity','1000000001']:
            with self.subTest(val=val):
                self.setUp();self.data['plan']['guests']=val;self.bad()
        self.setUp();self.data['recipes'][0]['ingredients'][0]['unit']='oz';self.bad('非法单位')
        self.setUp();self.data['recipes'][0]['ingredients'][0]['quantity']='一把';self.bad()
        self.setUp();self.data['recipes'][0]['ingredients'][0]['quantity']=-1;self.bad()
    def test_no_mass_volume_conversion(self):
        self.data['recipes'][0]['ingredients'][0]['unit']='g';self.bad('维度')
    def test_missing_plan(self):
        self.data['plan']=dict(guests=6);self.bad('确认饮用计划')
    def test_non_integer_cups(self):
        self.data['plan']['total_cups']='12.5';self.bad('整数')
    def test_alcohol_free(self):
        self.assertFalse(self.result()['recipes']['zero']['alcoholic'])
        del self.data['recipes'][0]['alcoholic'];self.bad('alcoholic')
    def test_reserves_dont_change_recipe(self):
        self.assertEqual(self.result()['recipes']['forest']['parsed'][0]['quantity'],D(40))
        self.assertEqual(self.rows()['lime']['procurement'],D(8))
    def test_ice_unknown_and_uncertain(self):
        self.data['config'].pop('ice')
        self.data['recipes'][0]['ingredients'][0].pop('stage')
        r=self.result();self.assertEqual(len(r['warnings']),3);self.assertNotIn('ice',self.rows())
    def test_escaping_paths_and_csv(self):
        self.data['event']='../../<script>alert("x")</script>'
        self.data['recipes'][0]['name']='<img src=x onerror=alert(1)> & "hi"'
        self.data['ingredients']['gin']['name']='=HYPERLINK("bad")'
        with tempfile.TemporaryDirectory() as t:
            p.write_outputs(self.result(),t)
            paths={x.name for x in Path(t).iterdir()}
            self.assertEqual(paths,{'bar-guide.md','shopping.md','shopping.csv','costs.csv','batches.md','menu.html','summary.json'})
            h=(Path(t)/'menu.html').read_text();self.assertNotIn('<script>',h);self.assertNotIn('<img ',h)
            self.assertIn('&lt;img',h);self.assertNotIn('140 HKD',h)
            with open(Path(t)/'shopping.csv',encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f))
            self.assertTrue(rows[1][1].startswith("'="));self.assertTrue(all(len(r)==13 for r in rows))
    def test_serving_only_cannot_premix(self):
        self.data['ingredients']['soda']['serving_only']=True
        self.data['recipes'][0]['ingredients'][3]['stage']='premix'
        self.bad('不能预混')
    def test_zero_weight(self):
        self.data['plan']['weights']=dict(forest=0,gold=0,zero=1)
        self.assertEqual(self.result()['counts']['zero'],12)
        self.data['plan']['weights']['zero']=0;self.bad('比例总和')
    def test_operational_guide_quantities_and_service(self):
        guide=p.bar_guide(self.result())
        self.assertIn('| 青柠汁 | 20 ml |',guide)
        self.assertIn('| 青柠汁 | 240 ml | 24 ml | 0 ml | 264 ml | 100 ml | 1 × 250 ml |',guide)
        self.assertIn('| 青柠汁 | 80 ml |',guide)
        self.assertIn('整批的 1/4',guide)
        self.assertIn('本杯摇和/搅拌用冰：100 g',guide)
        self.assertIn('本杯出杯用冰：120 g',guide)
        self.assertIn('鲜切装饰出杯时处理',guide)
        self.assertIn(self.data['recipes'][0]['method'],guide)
        self.assertEqual(guide.count('· 按此流程出 1 杯'),3)
    def test_guide_single_selected_drink_and_unknowns(self):
        self.data['plan']=dict(mode='per_guest',guests=4,servings_per_guest=dict(zero=1))
        self.data['config'].pop('ice')
        self.data['recipes'][2]['ingredients'][0]['stage']='uncertain'
        guide=p.bar_guide(self.result())
        self.assertNotIn('林间气泡',guide)
        self.assertIn('无酒精（按输入声明）',guide)
        self.assertIn('是否可预混待确认',guide)
        self.assertIn('用冰规则待确认',guide)
        self.assertNotIn('青柠汁 20 ml；糖浆',guide) # uncertain lime is not put into confirmed premix
    def test_duplicate_ice_source(self):
        self.data['recipes'][0]['ingredients'].append(dict(ingredient='ice',quantity='100',unit='g',stage='serve'))
        self.bad('避免重复计算')
    def test_generated_scenarios(self):
        for n,expected in [(4,[3,3,2]),(6,[4,4,4]),(10,[7,7,6])]:
            result=p.calculate(p.load(ROOT/f'examples/party-{n}.json'))
            self.assertEqual(list(result['counts'].values()),expected)
            self.assertEqual(result['guests'],n)
            self.assertEqual(sum(result['counts'].values()),n*2)
    def test_markdown_tables_are_contiguous(self):
        with tempfile.TemporaryDirectory() as t:
            p.write_outputs(self.result(),t)
            text=(Path(t)/'batches.md').read_text()
            self.assertNotIn('|\n\n|',text)
    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as t:
            f=Path(t)/'x.json';f.write_text('{"a":1,"a":2}')
            with self.assertRaises(p.InputError):p.load(f)
    def test_cli_error(self):
        with tempfile.TemporaryDirectory() as t:
            f=Path(t)/'bad.json';f.write_text('{"event": 3}')
            run=subprocess.run([sys.executable,str(ROOT/'party-bar-planner/scripts/planner.py'),str(f),'--out',str(Path(t)/'out')],capture_output=True,text=True)
            self.assertEqual(run.returncode,2);self.assertIn('错误',run.stderr)
            self.assertFalse((Path(t)/'out').exists())

if __name__=='__main__':unittest.main()
