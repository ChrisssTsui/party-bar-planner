# 输入格式 v1

Python 3.10+；UTF-8 JSON 单文件，同时包含配方、库存、计划。完整实例在仓库 `examples/party-6.json`。数字可写成 JSON 数字或十进制字符串；最多 16 位有效数字、6 位小数，非负且不大于 10 亿，不接受布尔/NaN/Infinity。人数、杯数须为整数。输出数量保留小数，金额以 80 位 Decimal 精度运算，展示到分（ROUND_HALF_UP），总计先汇总后舍入。配置率 0–1。

| 字段 | 要求与意义 |
|---|---|
| `event` | 非空活动名称 |
| `currency` | 三位大写单币种代码，如 HKD / CNY；不查询汇率 |
| `ingredients` | 非空对象，键为稳定原料 ID，不按相似名称猜测合并 |
| `aliases` | 可选对象，别名 → 正式原料 ID；禁止覆盖正式 ID、链式映射 |
| `recipes` | 非空配方数组 |
| `inventory` | 可选数组，默认无库存；同一正式 ID（含别名）只能出现一次 |
| `plan` | 必填；以下两种之一 |
| `config` | 可选，默认无安全余量、无装饰损耗、冰待确认 |

所有名称/说明为非空文本，最长 2000 字符。文件名固定，不从名称生成路径。JSON 重复键报错；不要使用未列出的字段（v1 尚未拒绝所有未知字段）。结构错误以退出码 2 与中文错误提示返回，不生成采购结果。

## 原料与库存

```json
{
  "ingredients": {
    "gin-a": {"name": "金酒 A · 700ml", "package_size": "700", "unit": "ml", "price": "140", "currency": "HKD"},
    "soda": {"name": "苏打水", "package_size": "330", "unit": "ml", "price": null, "serving_only": true}
  },
  "aliases": {"指定金酒": "gin-a"},
  "inventory": [{"ingredient": "gin-a", "quantity": "100", "unit": "ml"}]
}
```

`name/package_size/unit` 必填，包装量大于零；`price` 可省略或 null（未知），零仅表示明确免费。每包价格允许小数。原料 `currency` 缺省继承顶层；显式值必须一致，即使价格未知也不混币种。`serving_only` 可选布尔，默认 false，用于汽水、冰、鲜切装饰等出杯原料。

单位严格区分大小写：`ml` / `L` → ml；`g` / `kg` → g；`个` → 个。不支持 oz、dash、一把或密度换算。每个 ID 只能有一种单位维度；库存单位可以使用同维度的另一单位。不同酒款或不能互换的规格使用不同 ID。每个 ID 当前只有一种采购包装，不优化多个包装组合。多个包装/批次库存请先显式汇总为同一可替代原料的数量。

## 配方

```json
{
  "id": "fizz",
  "name": "林间气泡 / Forest Fizz",
  "alcoholic": true,
  "ingredients": [
    {"ingredient": "gin-a", "quantity": "40", "unit": "ml", "stage": "premix"},
    {"ingredient": "soda", "quantity": "60", "unit": "ml", "stage": "serve"}
  ],
  "method": "摇匀非碳酸原料，滤入杯中，出杯时加入苏打水与冰。",
  "garnish": "无",
  "glass": "高球杯"
}
```

所有顶层配方字段必填，ID 唯一；`alcoholic` 必须为布尔，由使用者核对原料是否符合无酒精要求，脚本不推断酒精度。原料行 `ingredient/quantity/unit` 必填，用量大于零；同 ID 重复行会相加，但阶段分别保留。`stage` 可为 `premix/serve/uncertain`，默认 uncertain 并提示待确认。`role` 可为 `base/garnish`，默认 base。装饰与 serving_only 原料禁止 premix。不认识的自然语言用量必须先确认，不能猜测。

`garnish` 文字不会自动增加采购需求。需采购的装饰另列原料行，如 `quantity: "0.5", unit: "个", stage: "serve", role: "garnish"`。

## 饮用计划

每位客人喝指定款数（只做列出的款）：

```json
{"mode":"per_guest","guests":6,"servings_per_guest":{"fizz":1,"zero":1}}
```

每款每人杯数是非负整数；人数大于零，最终至少一杯。此模式假设所有客人饮用计划相同；不同人群可先汇总为总杯数与相应权重。

总共制作指定杯数，并按比例分配：

```json
{"mode":"total_ratio","guests":6,"total_cups":12,"weights":{"fizz":"1","gold":"1","zero":"1"}}
```

权重非负、总和大于零；未列出的配方为零杯。最大余数法：按精确权重先向下取整，再按余数降序补足；同余数按配方 ID 字典序（与 JSON 顺序无关）。内部将权重转换成整数微单位，整数除法比较余数，无浮点误差。人数只作活动人数记录，不作为每款配方乘数。总杯数须独立提供，不能仅根据人数猜测。

## 余量、损耗和冰

```json
{
  "reserve_rate":"0.10",
  "garnish_loss_rate":"0.20",
  "ice": {
    "mixing":{"ingredient":"ice","quantity_per_cup":"100","unit":"g","recipes":["fizz","gold"]},
    "serving":{"ingredient":"ice","quantity_per_cup":"120","unit":"g","recipes":["fizz","gold","zero"]}
  }
}
```

以上对象放入 `config`。冰规则必须显式列出适用配方 ID（不可重复），以 g/kg 计量。适用配方中已列相同冰原料时会报错，防止重复计量；请只用一种来源。两种冰规则可以共用同一个采购 ID，但独立计算用途。未给规则分别输出待确认项。示例冰量只是演示参数，不是行业标准。

每个原料：

- 基础需求 = 各配方单杯用量 × 杯数之和 + 配置的冰量。
- 安全余量 = 基础需求 × reserve_rate（含冰和装饰）。
- 装饰损耗 = 标为 garnish 的基础需求 × garnish_loss_rate；与安全余量相加，不复利。
- 备料目标 = 基础 + 安全余量 + 损耗；个数采购需求先向上取整。
- 缺口 = max(0, 采购需求 − 库存)，包装数 = ceil(缺口 / 包装规格)。
- 基础使用后剩余 = 库存 + 买入包装总量 − 基础需求。
- 目标备料后剩余 = 库存 + 买入包装总量 − 备料目标（个数采购取整差保留在剩余中）。
- 耗用成本 = 基础需求 / 包装规格 × 每包价格（按当前单价重估库存；不计余量与损耗）。
- 采购支出 = 包装数 × 每包价格。未知价格空白；若不需要购买，采购支出可确认为零，但耗用成本仍未知。

价格缺失时只显示已知小计并标记“不完整”，不能当作完整总价。所有配置预留都列在采购清单，不修改单杯配方。确认真实价格、装饰和冰量后再将清单用于实际活动。
