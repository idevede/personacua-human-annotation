# PersonaCUA 人工标注网页

这个目录只放标注网页的代码，以及导入之后网页要读的运行文件。模型跑出来的原始结果在云盘上，下载解压后留在本目录外面，再用下面的导入脚本硬链接进 `assets/`。

公开仓库不包含 `assets/`，也不包含 `data/manifest.private.json`。前者是截图，克隆之后按第 3 节重新链接。后者是模型槽位对照，只留在跑服务的那台机器上。

当前这批结果是 9 个模型、22 个任务、每个任务 5 个人设（`expert`、`practical`、`senior`、`young`、`none`），共 110 个标注单元、990 条输出、38545 张截图。页面上只显示模型 A 到 I，模型真名写在 `data/manifest.private.json`。

五个人的人格分配和标注步骤在 [ASSIGNMENT.md](ASSIGNMENT.md)。

## 1. 从云盘下载

到存放运行结果的云盘，下载压缩包：

```text
personacua-annotation-subset.tar.gz
```

这份压缩包大约 2.4 GB。只下载这个标注子集。全量实验目录不用下。

## 2. 解压后放在哪里

把压缩包解压到**代码目录的上一级**，和本目录并列。解压结果不要放进 `assets/`。

```text
parent/
  personacua-human-annotation/     # 本目录：代码、manifest、链接进来的截图
  personacua-annotation-subset/    # 解压出来的运行结果
    subset.json
    runs/
      <model_key>/
        odysseys 或 real/
          <task_id>/
            <persona>/
              result.json
              shots/...
```

在压缩包所在目录执行：

```bash
tar -xzf personacua-annotation-subset.tar.gz
```

解压后的第一层必须直接是 `personacua-annotation-subset/subset.json` 和 `personacua-annotation-subset/runs/`。本机已经放在：

```text
/Users/defu.cao/Downloads/personacua-annotation-subset
```

`persona` 用结果里的原名。四个人设是 `art-expert` 这种 `{场景}-{角色}`。对照是 `none`，不是 `NAN`，也不是 `art-none`。`runs/` 下面的 `odysseys` 或 `real` 要和 `result.json` 里的 `suite_id` 一致。命名规则的完整说明在 [handoff/README.md](handoff/README.md)。

## 3. 把运行结果链接到网页

在本目录执行。导入会先清空 `assets/` 和两份 manifest，再按网页使用的文件名把截图硬链接进来：

```bash
cd /path/to/personacua-human-annotation

python3 handoff/check_subset.py \
  --subset ../personacua-annotation-subset

python3 tools/import_subset.py \
  --subset ../personacua-annotation-subset \
  --force
```

`check_subset.py` 退出码为 0，并打印 `"ok": true`，再跑导入。导入结束时 `materialization` 里应是 `hardlink`。这次导入的结果是 110 个 case、9 个模型、990 条输出、38545 张硬链接。

链接后的路径是：

```text
assets/<场景>--<task_id 前 12 位>--<角色>/<槽位>/trace/<序号>-<原文件名>
assets/<场景>--<task_id 前 12 位>--<角色>/<槽位>/final/<序号>-<原文件名>
```

例如 `assets/art--80257c727b8e--expert/A/trace/...`。槽位 A 到 I 按固定 seed 盲化，和云盘目录里的模型名不是同一个顺序。

硬链接和旁边的解压目录指向同一份截图，磁盘上只有一份。Finder 单独查看 `assets/` 仍会显示体积。如果解压目录和代码不在同一个磁盘，脚本会改成普通复制，输出里会出现 `copy-fallback`。

`data/guide.zh.json`、`data/scenarios.json` 和已经保存的 `annotations/` 不会被删掉。

## 4. 在本地打开网页

需要 Python 3，没有第三方包。在本目录执行：

```bash
./start.sh
```

浏览器打开 <http://127.0.0.1:8765>。

服务会读取 `data/manifest.public.json` 和 `assets/` 里的截图，并把标注写回 `annotations/`。直接用浏览器打开 `web/index.html` 时，这些接口不可用，页面加载不到数据。

在另一台机器上看本机服务时，保持默认的本机监听，再建立 SSH 隧道：

```bash
ssh -L 8765:127.0.0.1:8765 USER@SERVER
```

然后在个人电脑打开 <http://127.0.0.1:8765>。使用 VS Code、Cursor 等远程开发工具时，也可以在 **Ports / 端口** 面板转发远端的 `8765`，再点击 **Open in Browser**。

## 标注流程

1. 标注员输入自己的唯一姓名。
2. 页面加载该姓名已有的进度；新姓名会得到空白记录。
3. 先看所有模型相同的原始任务，再选择模型 A 到 I 中的一个。改写后的任务入口可能不同，一次只看当前选中的模型。
4. 对当前模型的每个 rubric 选择：
   - `得分`：`yes`
   - `不得分`：`no`
   - `证据不足`：`unsure`
5. 每个 rubric 带有提示，说明看到什么证据就可以判得分。下面还有一块「Claude Opus 5.5 的判断」，直接给出得分、不得分或证据不足，并写明打开第几张截图能看到什么。
6. 页面会自动保存。如果这条里还有模型没判完，会先切到那个模型；都判完了再进入下一条。

服务端结果保存到：

```text
annotations/<标注员姓名>.json
```

写入使用临时文件加原子替换。浏览器还会保存一份本地恢复副本，并提供“下载备份”按钮。不同标注员必须使用不同姓名；同名代表继续同一份记录。

服务端 JSON 中包含私有的模型槽位映射，便于之后直接按模型汇总。这个映射、自动评分和原始结果路径不会发给标注页面。

## Claude Opus 5.5 的判断

判断存在 `data/claude_notes.json`，按 case、模型槽位、rubric 存放。每条有 `decision`（`yes` / `no` / `unsure`）、`frames`（截图编号，和网页上的第 N 张一致）、`evidence` 和 `reason`。110 个 case、9 个模型、3960 条 rubric 都有。

这些判断是 Claude Opus 5.5 逐条看截图和最终回答写的，不是关键词匹配。判的时候只看公开的轨迹和截图，没有看自动评分和 Grok 的提示。只有先试做的 `art--80257c727b8e--practical` 这一条，判之前看到过模型对照，其余 109 条不知道哪个槽位是哪个模型。9 个模型里有一个就是 Claude Opus 5.5，它那一格是自己判自己。

重判某个模型时，先看轨迹，再把新的判断写回去：

```bash
python3 tools/show_trace.py <case_id> <槽位> --chars 900
python3 tools/show_trace.py <case_id> <槽位> --only 12,40 --chars 6000
python3 tools/save_claude_notes.py <case_id> <槽位> < notes.json
```

`show_trace.py` 打印任务、rubric、每张截图的页面文字和最终回答，不打印模型名。`save_claude_notes.py` 会检查 rubric 是否齐全、截图编号是否存在，再加锁合并进文件。服务会自动读到新文件，刷新网页即可。

## 快捷键

| 按键 | 功能 |
|---|---|
| `A` / `D` | 当前模型的上一张 / 下一张截图 |
| `1`–`9` | 切换模型 A–I |
| `[` / `]` | 上一个 / 下一个标注单元 |

点击截图可以放大。左侧可以按场景、Persona 和完成状态筛选，并跳到下一个未完成单元。

## 检查

```bash
node --check web/app.js
python3 -m py_compile server.py tools/import_subset.py tools/build_dataset.py
python3 -m unittest discover -s tests -v
```

服务器只使用 Python 标准库。它没有账号认证或 TLS，适合本机、SSH 隧道或可信内网使用。
