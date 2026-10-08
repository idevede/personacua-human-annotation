# 抽出 110 个标注单元，交给标注网页

把这个 `handoff/` 目录整个交给 Code Agent。它要在全量结果里找出我们选中的单元，重组到下面规定的目录里，通过 `check_subset.py`，再打成一个压缩包。

我们拿到压缩包后解压，运行一次导入，现有网页就会改成这些模型的盲测，不需要再改页面代码。

## 数量

不是 110 个不同的 task id。

- **22 个任务**，清单以 [selection.json](selection.json) 为准，不要自己另选任务。
- 每个任务 **5 个人设**：`expert`、`practical`、`senior`、`young`，再加上 NAN 对照。前四个的 id 是 `{scenario}-{role}`，例如 `art-expert`。对照在结果 JSON 里的字段值是 `none`，不是 `NAN`，也不是 `art-none`。
- 所以每个模型 **110 条**（22 × 5）。
- 模型个数不写死。同学那边有 8 个或 9 个就交 8 个或 9 个。某个模型必须 110 条齐全才放进子集；缺任意一条的模型不要塞进压缩包，写到旁边的 `partial_models.json`。

| tag | scenario | task_id | 人设 |
|---|---|---|---|
| odysseys | art | `80257c727b8e8c5426c1b03a2a4493231747e5d7` | art-expert, art-practical, art-senior, art-young, none |
| odysseys | art | `3868f9b52e96067b4f55834a3b110e1228b48e65` | 同上规则 |
| odysseys | community | `2cb0ed2a5df6053c6c982a5c5d436d25e006370f` | community-expert, community-practical, community-senior, community-young |
| odysseys | community | `140960bb7293bdeeb6bcc60931681cb9b815351b` | community-* |
| odysseys | ecommerce | `63d68bb25e279fc22e6e3592d8ca59add33b6eb1` | ecommerce-* |
| odysseys | ecommerce | `44f1e02116715d5fe313996811b358fe25bc3ee4` | ecommerce-* |
| odysseys | food | `082aa17f3e88c3ce10796244e3677c5643dd19c9` | food-* |
| real | food | `4fa0f20b6ff73e4a86442a1f0dc16ca4ab03582d` | food-* |
| odysseys | health | `69782bfcfdb3311496bc9048bf66915b33e692cd` | health-* |
| odysseys | health | `256342f13c0a03e080f92ee073153fe33a6881c0` | health-* |
| odysseys | lifestyle | `d8fe04d1cf29251d68382cde58f4424e80bad07c` | lifestyle-* |
| real | lifestyle | `36ba9459787fc18f62c6914cebc19d5dc91fe3f4` | lifestyle-* |
| real | productivity | `44d29d36275c65f1663b909b0a46e9a81e39e675` | productivity-* |
| real | productivity | `7ef23d555c843be90276866c577c29960ca72ec7` | productivity-* |
| real | professional | `d0ed06e50dabe8aba500009e0075e057cdf042d2` | professional-* |
| real | professional | `4b2245ba1a44a89e6a584c06cc267f08d850ff3c` | professional-* |
| odysseys | science | `e15345ed27f1933065af403601876e5f6597a943` | science-* |
| odysseys | science | `8c30f2f9ceeac75b05c725c5397022bb4f9d32a0` | science-* |
| odysseys | tech | `fbcfa176b2e1aa42200d4f3adb66dcf0a6ca62ee` | tech-* |
| odysseys | tech | `10548585c3214aa1a15f7ceef8aa4fde0c2fcdf7` | tech-* |
| odysseys | travel | `890a6880049a42684ac91a2e1809442846f9394c` | travel-* |
| real | travel | `1a625276b600a49df9521941df46cac8862790b9` | travel-* |

`tag` 只有 `odysseys` 和 `real`。拼写是 **odysseys**，不是 odessy。它必须和 `result.json` 里的 `suite_id` 一致。

## 网页实际读什么

标注网页不扫描原始实验目录。它读的是导入之后生成的 `data/manifest.public.json`。每条 case 里，每个模型占一个 `outputs[].slot`（A、B、C…）。页面上只显示「模型 A」，不显示模型真名。真名留在我们这边的私有清单里。

因此你交出来的压缩包里不要做盲测、不要改成 HTML。你只要把原始 `result.json` 和它引用的截图，放到下面这个目录形状里。我们解压后运行 `tools/import_subset.py`，由它生成网页用的 manifest 和 `assets/`。

## 你要写的程序

在有全量结果的机器上新建 `build_subset.py`，不要改本目录里的 `check_subset.py` 和 `subset_format.py`。程序接收两个参数：

```bash
python3 build_subset.py \
  --source /path/to/full-results \
  --out /path/to/personacua-annotation-subset
```

`--source` 是同学的全量结果根目录。它的内部层级不固定。不要假设一定是 `results/<model>/s1/<suite>/<task_id>/...`。用内容识别，不要用路径猜任务。

### 怎么找到一条结果

递归查找所有名为 `result.json` 的文件。读 JSON，只保留同时满足下面条件的文件：

- `task_id` 在 `selection.json` 的 22 个任务里
- `persona` 等于该任务的四个人设 id 之一，例如 `art-expert`，或者对照 `none`。对照在 JSON 里就是 `"persona": "none"`，目录名里常见 `none__模型名__...`。不要写成 `art-none`，也不要因为名字是 none 就丢掉
- `suite_id`（没有的话用 `suite_snapshot.id`）等于该任务的 `tag`
- `run` 是对象，而且 `run.trajectory` 是数组
- `run.stop_reason` 不是 `no_model_output`
- `run_config.agent.model` 是非空字符串

模型目录名必须用 `subset_format.model_key()`，不要自己另写一套替换规则。它取 `run_config.agent.model`，转成小写，把 `/` 换成 `-`。

同一个 `(model_key, task_id, persona)` 若有多份结果，用 `subset_format.run_sort_key()` 选最小的那份。规则是：丢掉 `no_model_output`；优先 `replicate_id` 为 `r1` 或 `first` 或空；再优先文件更新时间更晚的。

### 输出目录

压缩包的根目录必须直接包含 `subset.json` 和 `runs/`。解压后路径要长这样：

```text
personacua-annotation-subset/
  subset.json
  runs/
    <model_key>/
      <tag>/
        <task_id>/
          <persona>/
            result.json
            shots/...
```

例子：

```text
runs/grok-4.6/odysseys/80257c727b8e8c5426c1b03a2a4493231747e5d7/art-expert/result.json
runs/grok-4.6/odysseys/80257c727b8e8c5426c1b03a2a4493231747e5d7/none/result.json
runs/grok-4.6/odysseys/80257c727b8e8c5426c1b03a2a4493231747e5d7/art-expert/shots/000-00-open_tab.jpg
runs/muse-glimmer-30b/real/4fa0f20b6ff73e4a86442a1f0dc16ca4ab03582d/food-expert/result.json
```

复制规则：

- 把选中的那份 `result.json` 所在的整个目录复制到目标 `<persona>/` 下面。`result.json` 必须就在这一层，不能再套一层 run 名。
- 不要改 `result.json` 的内容，也不要重命名 `shots/` 里的文件。网页导入时按 JSON 里的相对路径找图，例如 `shots/000-00-open_tab.jpg` 是相对于 `result.json` 所在目录。
- 用 `subset_format.referenced_files()` 列出 `run.trajectory[].screenshot.file`、`run.final_screenshots[].file`、`run.real_state.file`。这些相对路径指向的文件必须一起复制，且不能是绝对路径，不能包含 `..`。
- 引用了但磁盘上不存在的文件，这条结果算失败，不要悄悄删掉引用。
- 没有截图的失败跑，只要不是 `no_model_output`，仍然保留 `result.json`。

`subset.json` 的形状：

```json
{
  "schema_version": "personacua.annotation-subset.v1",
  "selection_id": "personacua-22-tasks-v1",
  "models": [
    {"model_key": "grok-4.6", "display_name": "grok-4.6"}
  ],
  "cells": [
    {
      "model_key": "grok-4.6",
      "task_id": "80257c727b8e8c5426c1b03a2a4493231747e5d7",
      "tag": "odysseys",
      "scenario": "art",
      "role": "expert",
      "persona": "art-expert",
      "result": "runs/grok-4.6/odysseys/80257c727b8e8c5426c1b03a2a4493231747e5d7/art-expert/result.json"
    }
  ]
}
```

`result` 必须等于 `subset_format.result_relpath(model_key, cell)`。`cells` 的长度必须是 `模型数 × 110`。`models` 按 `model_key` 排序。`display_name` 用原始 `run_config.agent.model` 字符串。

某个模型不足 110 条时，不要放进 `subset.json`。在输出目录的上一级写 `partial_models.json`：

```json
{
  "partial_models": [
    {
      "model_key": "some-model",
      "found": 80,
      "missing": ["80257c727b8e8c5426c1b03a2a4493231747e5d7 none"]
    }
  ]
}
```

## 验收和打包

在 `handoff/` 目录里运行：

```bash
python3 check_subset.py --subset /path/to/personacua-annotation-subset
```

退出码必须是 0，并且打印 `"ok": true`。不要带 `--selection` 以外的替代清单。然后：

```bash
tar -C /path/to -czf personacua-annotation-subset.tar.gz personacua-annotation-subset
```

`tar -tzf` 的第一层应是 `personacua-annotation-subset/subset.json`，不能是多余的父目录，也不能把全量结果打进去。

## 我们收到压缩包之后

这一节不是同学机器上要跑的。操作说明在仓库根目录的 [README.md](../README.md)。解压目录和代码目录并列，再在标注项目里执行：

```bash
tar -xzf personacua-annotation-subset.tar.gz -C ..
python3 tools/import_subset.py --subset ../personacua-annotation-subset --force
./start.sh
```

导入会按固定 seed 把每个模型放进模型 A、B、C…，标注页看不到模型真名。已有的中文说明和已经保存的标注文件不会被删掉。`--force` 会清空并重建 `assets/`，同时替换两份 manifest。截图用硬链接，指向解压目录里的原文件。
