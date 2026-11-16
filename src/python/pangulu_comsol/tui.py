"""Interactive terminal application; operations use the same public CLI."""
import asyncio
import json
import os
import pathlib
import re
import signal
import sys
from datetime import datetime
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, RichLog, Select, SelectionList, Static, Switch, TabbedContent, TabPane, Tabs
from textual.worker import get_current_worker
from . import __version__
from . import cli
from . import runtime as core
from . import testing


class ActionButton(Button):
    """Mark the actual focus independently of a button's purpose."""

    def __init__(self, label, **kwargs):
        self.title = label
        super().__init__("  " + label, **kwargs)

    def on_focus(self):
        self.label = "▶ " + self.title

    def on_blur(self):
        self.label = "  " + self.title


class Details(ModalScreen[None]):
    CSS = """
    Details { align: center middle; }
    #details-box { width: 92%; height: 85%; border: round $primary; background: $surface; padding: 1; }
    #details-log { height: 1fr; }
    """
    BINDINGS = [("escape", "close", "关闭")]

    def __init__(self, output):
        super().__init__()
        self.output = output

    def compose(self) -> ComposeResult:
        with Vertical(id="details-box"):
            yield Label("上次操作详情")
            yield RichLog(id="details-log", wrap=True, min_width=20)
            yield ActionButton("关闭", id="close-details")

    def on_mount(self):
        self.query_one("#details-log", RichLog).write(Text(self.output or "暂无操作输出"))

    def action_close(self):
        self.dismiss(None)

    @on(Button.Pressed, "#close-details")
    def close_button(self, event):
        event.stop()
        self.action_close()


class PanguComsolApp(App[int]):
    TITLE = "Pangu–COMSOL"
    SUB_TITLE = "配置 · SDK 包 · 授权 · 运行"
    BINDINGS = [
        Binding("up", "navigate(-1)", "上移", show=False, priority=True),
        Binding("down", "navigate(1)", "↑↓ 移动", priority=True),
        Binding("escape", "focus_tabs", "页签", show=False, priority=True),
        ("f1", "show_tab('overview')", "概览"),
        ("f2", "show_tab('configuration')", "配置"),
        ("f3", "show_tab('launch')", "运行"),
        ("f4", "show_tab('packages')", "SDK"),
        ("f5", "show_tab('licences')", "授权"),
        ("f6", "refresh", "刷新"),
        ("f7", "check", "检查"),
        ("f8", "show_tab('tests')", "测试"),
        ("ctrl+c", "cancel", "取消"),
        ("ctrl+q", "quit", "退出"),
    ]
    CSS = """
    Screen { layout: vertical; }
    #data-home { height: 1; color: $text-muted; padding: 0 1; }
    #pages { height: 1fr; min-height: 10; }
    TabPane { padding: 1 2; }
    VerticalScroll { height: 1fr; }
    .card { height: auto; padding: 1; border: round $border-blurred; margin-bottom: 1; }
    .helper { height: auto; color: $text-muted; margin: 0 0 1 0; }
    .field-label { height: 1; }
    Input, Select { margin-bottom: 1; }
    .actions { height: auto; min-height: 3; margin: 1 0; }
    .actions Button { width: auto; min-width: 14; margin-right: 1; }
    ActionButton { background: $surface-lighten-1; border: tall $border-blurred; }
    ActionButton:focus { background: #2563eb; color: #ffffff; border: tall #93c5fd; text-style: bold; }
    Input:focus, Switch:focus, Select:focus > SelectCurrent { border: tall #93c5fd; }
    Tab.-active { color: $success; text-style: bold; }
    Tabs:focus Tab.-active { background: #2563eb; color: #ffffff; }
    Underline > .underline--bar { color: $success; }
    .switch-row { height: 3; align-vertical: middle; }
    .switch-row Label { width: 1fr; padding-top: 1; }
    #licence-list { height: 7; margin-bottom: 1; }
    #licence-list > .option-list--option-highlighted { background: $surface; }
    #licence-list:focus { border: tall #93c5fd; }
    #licence-list:focus > .option-list--option-highlighted { background: #2563eb; color: #ffffff; }
    #navigation-hint { height: auto; max-height: 2; padding: 0 1; color: $text-muted; }
    #operation { height: 1; padding: 0 1; color: $accent; }
    #activity { height: 5; min-height: 3; border-top: solid $border-blurred; margin: 0 1; }
    """

    def __init__(self):
        super().__init__()
        self.data_home = core.ROOT
        self.busy = False
        self.last_output = ""
        self.last_result = None
        self._refresh_error = ""
        self.operation_process = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(Text(f"应用目录  {self.data_home}"), id="data-home")
        with TabbedContent(id="pages"):
            with TabPane("概览", id="overview"):
                with VerticalScroll():
                    yield Static("正在读取安装状态…", id="overview-state", classes="card")
                    yield Static("首次使用：初始化配置 → 导入本地 SDK → 导入原始许可 → 检查 → 启动 COMSOL。", classes="helper")
                    with Horizontal(classes="actions"):
                        yield ActionButton("初始化配置", id="initialize", classes="operation-button")
                        yield ActionButton("检查环境", id="check", classes="operation-button")
                        yield ActionButton("刷新", id="refresh")
                    with Horizontal(classes="actions"):
                        yield ActionButton("操作详情", id="details")
            with TabPane("配置", id="configuration"):
                with VerticalScroll():
                    yield Label("COMSOL 安装目录（留空自动查找）", classes="field-label")
                    yield Input(placeholder="/path/to/multiphysics", id="comsol-path")
                    yield Label("CUDA 目录（包含 lib64/）", classes="field-label")
                    yield Input(placeholder="runtime/cuda 或已安装的 CUDA 目录", id="cuda-path")
                    with Horizontal(classes="switch-row"):
                        yield Label("在 GUI 中提供 Pangu 求解器选项")
                        yield Switch(True, id="global-options")
                    yield Static("只修改本模块配置；COMSOL 的偏好、临时、恢复和工作区设置保持原有行为。", classes="helper")
                    with Horizontal(classes="actions"):
                        yield ActionButton("保存配置", id="save-config", classes="operation-button")
                        yield ActionButton("查找 COMSOL", id="discover", classes="operation-button")
            with TabPane("运行", id="launch"):
                with VerticalScroll():
                    yield Label("模型文件（可留空）", classes="field-label")
                    yield Input(placeholder="/path/to/model.mph", id="model-path")
                    yield Label("求解后端", classes="field-label")
                    yield Select([("Pangu", "pangu"), ("NVIDIA cuDSS", "cudss")], value="pangu", allow_blank=False, id="backend")
                    yield Label("Pangu 执行方式",classes="field-label")
                    yield Select([("worker 独立进程","worker"),("直接封装，同进程","direct")],value="worker",allow_blank=False,id="execution")
                    yield Static("直接模式为本次启动取消 libjsig 预加载，使用同一份原始 SDK 与授权。",classes="helper")
                    yield Label("线程数（留空继承原有设置）", classes="field-label")
                    yield Input(placeholder="继承原有设置", id="threads")
                    yield Static("COMSOL 会独立运行，启动提交后可继续使用菜单。带模型路径时打开工作副本；在 COMSOL 中选择 Pangu 后计算。", classes="helper")
                    with Horizontal(classes="actions"):
                        yield ActionButton("启动 COMSOL", id="launch-gui", classes="operation-button")
            with TabPane("SDK 包", id="packages"):
                with VerticalScroll():
                    yield Static("正在读取 SDK…", id="sdk-state", classes="card")
                    yield Label("用户已下载的原始发行包", classes="field-label")
                    yield Input(placeholder="/path/to/pangulu-release.tar.gz", id="package-path")
                    yield Static("仅导入本地原包，不内置下载网址。更新会先校验，再整体替换 SDK；许可、配置和运行结果保留。", classes="helper")
                    with Horizontal(classes="actions"):
                        yield ActionButton("首次导入", id="install-sdk", classes="operation-button")
                        yield ActionButton("更新已有 SDK", id="update-sdk", classes="operation-button")
                    yield Static("配套组件未提供时，可用已安装的 CUDA 开发头文件、gcc/g++ 和 COMSOL 构建运行组件。", classes="helper")
                    with Horizontal(classes="actions"):
                        yield ActionButton("构建运行组件", id="build-module", classes="operation-button")
            with TabPane("授权", id="licences"):
                with VerticalScroll():
                    yield Label("原始许可文件", classes="field-label")
                    yield Input(placeholder="/path/to/customer.key", id="licence-path")
                    with Horizontal(classes="switch-row"):
                        yield Label("替换目录中不同内容的同名原件")
                        yield Switch(False, id="replace-licence")
                    with Horizontal(classes="actions"):
                        yield ActionButton("导入许可", id="import-licence", classes="operation-button")
                    yield SelectionList[str](id="licence-list")
                    yield Static("勾选后保存选择，或使用全部许可。文件内容保持原样，授权由厂商 SDK 校验。", classes="helper")
                    with Horizontal(classes="actions"):
                        yield ActionButton("保存选择", id="select-licences", classes="operation-button")
                        yield ActionButton("自动使用全部", id="auto-licences", classes="operation-button")
            with TabPane("测试",id="tests"):
                with VerticalScroll():
                    yield Static("正在读取可选测试组件…",id="test-state",classes="card")
                    yield Label("测试后端",classes="field-label")
                    yield Select([(label,name) for name,label in testing.BACKENDS.items()],value="cudss",allow_blank=False,id="test-backend")
                    yield Label("Pangu 执行方式",classes="field-label")
                    yield Select([("worker 独立进程","worker"),("直接封装，同进程","direct")],value="worker",allow_blank=False,id="test-execution",disabled=True)
                    yield Label("GUI 模型文件（可留空）",classes="field-label")
                    yield Input(placeholder="/path/to/model.mph",id="test-model-path")
                    yield Label("线程数（GUI 留空继承，数值测试留空用 8）",classes="field-label")
                    yield Input(placeholder="继承原设置 / 数值测试 8",id="test-threads")
                    yield Static("cuDSS 封装调用原生 NVIDIA 核心；Pangu 可选择执行方式。GUI 中选择对应 Direct 节点，模型路径打开工作副本。",classes="helper")
                    with Horizontal(classes="actions"):
                        yield ActionButton("启动测试 GUI",id="launch-test",classes="operation-button")
                        yield ActionButton("构建 cuDSS 校准组件",id="build-tests",classes="operation-button")
                    yield Label("S 档验收工程目录",classes="field-label")
                    yield Input(placeholder="/path/to/study3-pangulu-s",id="test-project-path")
                    yield Select([("20 kHz 单频","20000"),("完整 59 频点","all")],value="20000",allow_blank=False,id="test-frequencies")
                    yield Static("S 档 Study 3 验收复用已有 CFD/Mapping，逐频核对四场、有限性和实际库映射。测试源码与模型单独提供。",classes="helper")
                    with Horizontal(classes="actions"):
                        yield ActionButton("运行数值测试",id="benchmark-test",classes="operation-button")
        yield Static("就绪", id="operation")
        yield RichLog(id="activity", max_lines=100, wrap=True, min_width=20)
        yield Static("↑↓ 移动  Enter 操作  空格切换  Esc 页签 · 蓝框/▶ 焦点，✓ 已勾选", id="navigation-hint")
        yield Footer()

    def on_mount(self):
        for scroll in self.query(VerticalScroll):
            scroll.can_focus = False
        self.query_one("#activity", RichLog).can_focus = False
        self.update_execution_choices()
        self.refresh_state(reload_forms=True)
        self.call_after_refresh(self.focus_page)

    def update_execution_choices(self):
        self.query_one('#execution',Select).disabled=self.busy or self.query_one('#backend',Select).value!='pangu'
        self.query_one('#test-execution',Select).disabled=self.busy or self.query_one('#test-backend',Select).value!='pangu'

    @on(Select.Changed,'#backend')
    @on(Select.Changed,'#test-backend')
    def backend_changed(self,event):
        self.update_execution_choices()

    def focus_page(self, name=None):
        pane=self.query_one('#'+(name or self.query_one('#pages',TabbedContent).active),TabPane)
        for widget in pane.query('Input, Select, Switch, SelectionList, Button'):
            if isinstance(widget, (Input, Select, Switch, SelectionList, Button)):
                if widget.focusable:
                    self.screen.set_focus(widget)
                    break

    def check_action(self, action, parameters):
        if action in {"navigate", "focus_tabs", "show_tab"}:
            if isinstance(self.screen, ModalScreen):
                return False
            # An open dropdown owns its arrows and Escape until it is closed.
            if any(select.expanded for select in self.query(Select)):
                return False
        if action == "navigate":
            focused = self.focused
            if isinstance(focused, SelectionList):
                if not focused.option_count:
                    return True
                boundary = 0 if parameters[0] < 0 else focused.option_count - 1
                return focused.highlighted == boundary
            if isinstance(focused, OptionList):
                return False
        return super().check_action(action, parameters)

    def action_navigate(self, direction):
        selector = "Tabs, Input, Select, Switch, SelectionList, Button"
        if direction < 0:
            self.screen.focus_previous(selector)
        else:
            self.screen.focus_next(selector)

    def action_focus_tabs(self):
        self.query_one(Tabs).focus()

    def action_show_tab(self, name):
        self.query_one("#pages", TabbedContent).active = name
        self.focus_page(name)

    def action_refresh(self):
        self.refresh_state()

    def action_check(self):
        self.request_operation(["doctor", "--json"], "环境检查")

    def action_quit(self):
        if self.busy:
            self.notify("当前操作尚未完成，请稍候再退出", severity="warning")
            return
        self.exit(0)

    def action_cancel(self):
        if not self.busy:
            self.exit(0)
        elif self.operation_process is not None and self.operation_process.returncode is None:
            try:
                os.killpg(self.operation_process.pid, signal.SIGINT)
                self.record_event("已请求取消，等待当前操作收尾")
            except ProcessLookupError:
                pass

    def record_event(self, value):
        self.query_one("#activity", RichLog).write(Text(datetime.now().strftime("%H:%M:%S ") + value))

    @work(thread=True, group="refresh", exclusive=True, exit_on_error=False)
    def refresh_state(self, reload_forms=False):
        try:
            state = {"settings": core.settings(), "package": cli.package_info(), "licences": cli.list_licences(), "tests":testing.status(),
                     "configured": cli.config_file().is_file()}
        except (OSError, ValueError) as error:
            state = {"error": str(error)}
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self.apply_state, state, reload_forms)

    def apply_state(self, state, reload_forms=False):
        if "error" in state:
            self.query_one("#overview-state", Static).update(Text("配置读取失败：" + state["error"]))
            if state["error"] != self._refresh_error:
                self.record_event(state["error"])
            self._refresh_error = state["error"]
            return
        self._refresh_error = ""
        settings, package, licences = state["settings"], state["package"], state["licences"]
        sdk = package["sdk"]
        sdk_label = sdk.get("version", "未安装")
        module_label = package["module_version"] or "未提供 / 未构建"
        selected = sum(item["selected"] for item in licences)
        overview = (f"Pangu–COMSOL {__version__}\n"
                    f"配置：{'已初始化' if state['configured'] else '尚未初始化'}\n"
                    f"COMSOL：{settings.get('comsol_root') or '自动查找'}\n"
                    f"SDK：{sdk_label}    运行组件：{module_label}\n"
                    f"许可：{selected} / {len(licences)} 份已选")
        self.query_one("#overview-state", Static).update(Text(overview))
        sdk_text = f"Pangu SDK：{sdk_label}\n{sdk.get('path', sdk.get('message', ''))}\n运行组件：{module_label}"
        self.query_one("#sdk-state", Static).update(Text(sdk_text))
        tests=state["tests"]
        self.query_one("#test-state",Static).update(Text("cuDSS 校准组件："+tests["directory"]+"\nPangu 运行组件："+tests['pangu_directory']+"\n"+"\n".join(
            item["label"]+"："+("可用" if item["available"] else "未就绪") for item in tests["backends"])))
        choices = self.query_one("#licence-list", SelectionList)
        choices.clear_options()
        choices.add_options([(Text(item["name"]), item["name"], item["selected"]) for item in licences])
        if reload_forms:
            self.query_one("#comsol-path", Input).value = settings.get("comsol_root", "")
            self.query_one("#cuda-path", Input).value = settings.get("cuda_root", "")
            self.query_one("#global-options", Switch).value = settings.get("gui_global_options", True)

    def file_argument(self, widget):
        value = self.query_one(widget, Input).value.strip()
        if not value:
            raise ValueError("请填写本地文件路径")
        path = pathlib.Path(value).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("所选路径不是文件")
        return str(path)

    def setting_path(self, value):
        if not value:
            return ""
        path = pathlib.Path(value).expanduser()
        return str(path if path.is_absolute() else self.data_home / path)

    @on(Button.Pressed)
    def button_pressed(self, event):
        event.stop()
        action = event.button.id
        try:
            if action == "refresh":
                self.refresh_state()
            elif action == "details":
                self.push_screen(Details(self.last_output))
            elif action == "initialize":
                self.request_operation(["init"], "初始化配置", reload_forms=True)
            elif action == "check":
                self.action_check()
            elif action == "discover":
                self.request_operation(["discover"], "查找 COMSOL")
            elif action == "save-config":
                payload = {"comsol_root": self.setting_path(self.query_one("#comsol-path", Input).value.strip()),
                           "gui_global_options": self.query_one("#global-options", Switch).value}
                cuda = self.query_one("#cuda-path", Input).value.strip()
                if cuda:
                    payload["cuda_root"] = self.setting_path(cuda)
                self.request_operation(["config", "apply", json.dumps(payload, ensure_ascii=False)], "保存配置", reload_forms=True)
            elif action in {"install-sdk", "update-sdk"}:
                verb = "install" if action == "install-sdk" else "update"
                self.request_operation(["package", verb, self.file_argument("#package-path")], "导入 SDK" if verb == "install" else "更新 SDK")
            elif action == "build-module":
                self.request_operation(["build"], "构建运行组件")
            elif action == "import-licence":
                args = ["license", "add", self.file_argument("#licence-path")]
                if self.query_one("#replace-licence", Switch).value:
                    args.append("--replace")
                self.request_operation(args, "导入许可")
            elif action == "select-licences":
                selected = self.query_one("#licence-list", SelectionList).selected
                if not selected:
                    raise ValueError("请选择至少一份许可，或点击自动使用全部")
                self.request_operation(["license", "use", *selected], "保存许可选择")
            elif action == "auto-licences":
                self.request_operation(["license", "auto"], "自动选择许可")
            elif action == "launch-gui":
                args = ["run", "--detach", "--backend", str(self.query_one("#backend", Select).value)]
                if self.query_one('#backend',Select).value=='pangu':args+=['--execution',str(self.query_one('#execution',Select).value)]
                count = self.query_one("#threads", Input).value.strip()
                if count:
                    if not count.isdecimal() or int(count) < 1:
                        raise ValueError("线程数须为正整数，或留空继承原有设置")
                    args += ["--threads", count]
                if self.query_one("#model-path", Input).value.strip():
                    args.append(self.file_argument("#model-path"))
                self.request_operation(args, "提交 COMSOL 启动")
            elif action == "build-tests":
                self.request_operation(["test","build"],"构建可选测试组件")
            elif action in {"launch-test","benchmark-test"}:
                backend=str(self.query_one("#test-backend",Select).value)
                verb="run" if action=="launch-test" else "benchmark"
                args=["test",verb,"--backend",backend]
                if backend=='pangu':args+=['--execution',str(self.query_one('#test-execution',Select).value)]
                count=self.query_one("#test-threads",Input).value.strip()
                if count:
                    if not count.isdecimal() or int(count)<1:raise ValueError("线程数须为正整数，或留空")
                    args += ["--threads",count]
                if verb=="run":
                    args.append("--detach")
                    if self.query_one("#test-model-path",Input).value.strip():args.append(self.file_argument("#test-model-path"))
                else:
                    project=pathlib.Path(self.query_one("#test-project-path",Input).value.strip()).expanduser()
                    if not self.query_one("#test-project-path",Input).value.strip() or not project.is_dir():raise ValueError("请填写有效的 S 档验收工程目录")
                    args += ["--project",str(project.resolve()),"--frequencies",str(self.query_one("#test-frequencies",Select).value)]
                self.request_operation(args,"提交测试 GUI 启动" if verb=="run" else "S 档数值测试")
        except (ValueError, OSError) as error:
            self.notify(str(error), severity="error")
            self.record_event(str(error))

    def request_operation(self, args, label, reload_forms=False):
        if self.busy:
            self.notify("当前操作尚未完成", severity="warning")
            return
        self.busy = True
        self.last_result = None
        self.query_one("#operation", Static).update(Text(label + "…"))
        for button in self.query(".operation-button"):
            button.disabled = True
        for field in self.query("Input, Switch, Select, SelectionList"):
            field.disabled = True
        self.record_event(label)
        self.execute_operation(args, label, reload_forms)

    @work(group="operation", exit_on_error=False)
    async def execute_operation(self, args, label, reload_forms):
        command = [sys.executable, "-B", "-m", "pangulu_comsol", "--home", str(self.data_home), *args]
        env = dict(os.environ)
        package_parent = str(pathlib.Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = package_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
                        cwd=str(self.data_home) if self.data_home.is_dir() else os.getcwd(), start_new_session=True)
            self.operation_process = process
            output, _ = await process.communicate()
            text = output.decode("utf-8", errors="replace")
            self.last_output = text[-100000:]
            self.last_result = {"args": args, "returncode": process.returncode, "output": text}
            if process.returncode in {130, -signal.SIGINT}:
                self.record_event(label + "已取消，刷新状态确认当前结果")
                self.notify(label + "已取消", severity="warning")
                self.query_one("#operation", Static).update(Text(label + "已取消"))
                self.refresh_state()
            elif process.returncode:
                error = next((line for line in reversed(text.splitlines()) if line.strip()), "操作失败")
                self.record_event(label + "失败：" + error)
                self.notify(label + "失败，查看操作详情", severity="error")
                self.query_one("#operation", Static).update(Text(label + "失败"))
            else:
                summary = label + "完成"
                if args[0] == "discover":
                    found = json.loads(text)
                    if len(found) == 1:
                        self.query_one("#comsol-path", Input).value = found[0]
                        summary = "找到 COMSOL，已填入配置页；保存后生效"
                    else:
                        summary = f"发现 {len(found)} 份安装，详见操作详情"
                elif args[0] == "run" or args[:2]==["test","run"]:
                    summary = "COMSOL 启动已提交；进程独立运行，可继续使用菜单"
                elif args[0] == "doctor":
                    report = json.loads(text)
                    device = re.search(r"NATIVE_DEVICE ordinal=\d+ name=(.*?) runtime=", report.get("stdout", ""))
                    summary = "环境检查通过" + ("：" + device[1] if device else "")
                self.record_event(summary)
                self.notify(summary)
                self.query_one("#operation", Static).update(Text(summary))
                self.refresh_state(reload_forms=reload_forms)
        except (OSError, ValueError) as error:
            self.last_output = str(error)
            self.last_result = {"args": args, "returncode": 1, "output": str(error)}
            self.record_event(label + "失败：" + str(error))
            self.notify(str(error), severity="error")
        finally:
            self.operation_process = None
            self.busy = False
            for button in self.query(".operation-button"):
                button.disabled = False
            for field in self.query("Input, Switch, Select, SelectionList"):
                field.disabled = False
            self.update_execution_choices()
