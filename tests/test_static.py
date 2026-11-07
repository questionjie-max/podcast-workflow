"""静态资源完整性:首页资产必须版本化且由服务端注入版本(回归:regex 写坏 ?v= 事故)。"""

import re

import pytest
from fastapi.testclient import TestClient

from podcast_studio.server.app import app


@pytest.fixture()
def client(studio_home, fake_tts):
    return TestClient(app)


def test_index_assets_versioned_and_existing(client):
    html = client.get("/").text
    # 1) 不允许出现写坏的 regex 字面量与未注入的占位符(生产事故回归)
    assert "\\?v=" not in html and "\\__BUILD__" not in html
    assert "__PS_VERSION__" not in html
    # 2) style/i18n/app 三个资产引用必须带语义化版本号(服务端注入)
    refs = re.findall(r'(?:style\.css|i18n\.js|app\.js)\?v=(\d+\.\d+\.\d+)', html)
    assert len(refs) == 3, f"资产引用缺失或未版本化: {refs}"
    # 3) 构建号必须与服务端版本完全一致(单一事实源注入;否则前端触发自愈刷新)
    build = re.search(r'window\.__BUILD__ = "(\d+\.\d+\.\d+)"', html)
    assert build, "__BUILD__ 缺失"
    health = client.get("/api/health").json()
    assert build.group(1) == health["version"]
    assert len(set(refs)) == 1 and refs[0] == health["version"]
    # 4) 引用的静态文件必须真实可取
    for path in ("/static/style.css", "/static/i18n.js", "/static/app.js"):
        assert client.get(path).status_code == 200, path


def test_settings_layering_common_first_and_advanced_folded(client):
    """P1-1 设置页分层:常用区在首屏,进阶整体默认折叠,展开后功能与字段不减少。"""
    html = client.get("/").text
    settings = html.split('id="view-settings"', 1)[1].split("</section>", 1)[0]

    # 1) 进阶抽屉存在且默认收起(标签上没有 open 属性)
    m = re.search(r'<details[^>]*id="settings-advanced"[^>]*>', settings)
    assert m, "进阶折叠区缺失"
    assert "open" not in m.group(0), "进阶区应默认折叠"

    # 2) 常用区都在进阶抽屉之前,顺序:一键连接 → 我的声音 → 音色墙 → 用量估算
    adv_at = settings.index('id="settings-advanced"')
    common_markers = ('id="paste-key"', 'id="clone-file"', 'id="voice-wall-fold"', 'id="price-currency"')
    positions = [settings.index(marker) for marker in common_markers]
    assert all(p < adv_at for p in positions), "常用区元素应位于进阶区之前"
    assert positions == sorted(positions), f"常用区顺序不对: {list(zip(common_markers, positions))}"

    # 3) 功能与字段不减少:原各卡的关键锚点全部仍在设置页(折叠只是收起,不是删除)
    for marker in (
        "keymatch-report",                 # 一键连接
        "cfg-preset",                      # 接口配置(专家)
        "search-provider",                 # 搜索
        "sched-enabled",                   # 定时生成
        "tts-ali-key", "tts-asr-appid",    # TTS / ASR
        "clone-list",                      # 我的声音
        "voice-wall",                      # 音色墙
        "bgm-path",                        # 垫乐
        "price-currency",                  # 用量估算
        "sys-info",                        # 系统信息
        "trust-statement",                 # 信任页
    ):
        assert marker in settings, f"设置页丢失字段/区域: {marker}"

    # 4) 音色墙折叠结构与 JS 展开接线(从新建页跳转不被折叠挡住)
    assert 'id="voice-wall-fold"' in settings
    app_js = client.get("/static/app.js").text
    assert "voice-wall-fold" in app_js


def test_done_page_hierarchy_single_cta_and_folded_extras(client):
    """P1-2 完成页信息层级:首屏唯一主 CTA,导出/Shownotes/分段细修收进折叠,内容不丢。"""
    app_js = client.get("/static/app.js").text
    done_tpl = app_js.split('if (state === "done")', 1)[1].split('if (state === "error")', 1)[0]

    # 1) 首屏唯一主 CTA(下载);打包下载/去电台不得再占首屏 actions
    assert done_tpl.count('class="cta"') == 1, "完成页主 CTA 应唯一"
    first_actions = done_tpl.split('<div class="actions">', 1)[1].split("</div>", 1)[0]
    for gone in ("exportEpisode", "openLibrary"):
        assert gone not in first_actions, f"{gone} 应移出首屏 actions"

    # 2) 导出与发布、分段细修各收进一个折叠(内容不丢,只是收起)
    assert done_tpl.count("<details") == 2, "完成页应恰好两个折叠区"
    assert 'class="done-fold"' in done_tpl
    for kept in ("exportEpisode", "openLibrary", "ep-shownotes", "copyShownotes",
                 "saveShownotes", "ep-chunks", "loadChunks"):
        assert kept in done_tpl, f"折叠区丢失功能: {kept}"

    # 3) 校准提示与用量合并为一行灰字(不再各占一行)
    assert "metaLine" in done_tpl and "usageText(st)" in done_tpl
    assert done_tpl.count('class="hint small"') == 1


def test_new_page_three_wizard_groups_first_open(client):
    """P1-3 新建页控件密度:六段平铺收成三组折叠,默认只展开第一组,功能锚点不丢。"""
    html = client.get("/").text
    new_view = html.split('id="view-new"', 1)[1].split("</section>", 1)[0]

    # 1) 三组 wizard-group;只有第一组默认展开(open 属性只在第一个 details 上)
    groups = re.findall(r'<details class="wizard-group"([^>]*)>', new_view)
    assert len(groups) == 3, f"应恰好三组折叠,得到 {len(groups)}"
    assert "open" in groups[0], "第一组(做什么)应默认展开"
    assert not any("open" in g for g in groups[1:]), "后两组应默认收起"

    # 2) 组内功能锚点不丢(默认值仍在 DOM,收起也能直接开工)
    for marker in ("style-cards", "mood-cards", "structure-select", "voice-select",
                   "minutes-select", "pace-select"):
        assert marker in new_view, f"新建页丢失控件: {marker}"

    # 3) 主 CTA 在折叠组之外(任何 details 之外),始终可直接点
    cta_at = new_view.index('id="btn-create"')
    for m in re.finditer(r'<details class="wizard-group"[^>]*>.*?</details>', new_view, re.S):
        assert not (m.start() < cta_at < m.end()), "主 CTA 不应被包进折叠组"


def test_library_row_inline_icons_folded_into_menu(client):
    """P1-4 电台列表降噪:行内只留 试听/打开,其余动作收进 ⋯ 菜单,功能不减少。"""
    app_js = client.get("/static/app.js").text
    row_tpl = app_js.split("const row = (ep, isDemo)", 1)[1].split("// 示例电台独立分区", 1)[0]

    # 1) 行内 icon 按钮只剩 ▶️ 与 📂;⬇️📦🗃️📤🗑️ 不再直接平铺在行内
    assert row_tpl.count('<button class="icon-btn"') == 2, "行内应只剩 试听/打开 两个按钮"
    for gone in ('>⬇️</button>', '>📦</button>', '>🗃️</button>', '>🗑️</button>', '>📤</button>'):
        assert gone not in row_tpl, f"行内仍平铺图标按钮: {gone}"

    # 2) ⋯ 菜单包含全部原动作(下载/打包/改名/归档/删除),点击外部可收起
    assert 'class="row-menu"' in row_tpl and '>⋯</summary>' in row_tpl
    for action in ("downloadEpisode", "exportEpisode", "renameEpisode",
                   "toggleArchive", "deleteEpisode"):
        assert action in row_tpl, f"⋯ 菜单丢失功能: {action}"
    assert 'row-menu[open]' in app_js and "d.open = false" in app_js


def test_empty_states_unified_component(client):
    """P1-5 空态统一:无节目/无匹配/无克隆 三处都走 emptyState(解释+主按钮+次要链接)。"""
    app_js = client.get("/static/app.js").text

    # 1) 统一组件存在且三处接线
    assert "function emptyState(" in app_js
    assert app_js.count("emptyState(") >= 4, "emptyState 组件 + 至少三处调用"

    # 2) 每个空态都有主按钮动作(下一步做什么)与次要链接
    for fn in ("clearLibrarySearch", "gotoRecording"):
        assert f"function {fn}(" in app_js, f"空态主按钮动作缺失: {fn}"
    for anchor in ("showView('new')", "showDemoShelf();return false",
                   "openVoiceWall();return false"):
        assert anchor in app_js, f"空态次要链接缺失: {anchor}"

    # 3) 旧的无动作空态文案不再出现
    assert "没有匹配「" not in app_js.replace('t("empty.nomatch.text")', ""), "无匹配空态应走词典"


def test_mobile_toast_rule_order(client):
    """P1-6 回归:#toast 基础规则必须在移动端覆盖规则(top:130px)之前——同特异性时
    源码后者覆盖前者,曾致移动端 130px 被 70px 覆盖、toast 被双行 header 遮挡。"""
    css = client.get("/static/style.css").text
    base = css.index("#toast {")
    override = css.index("top: 130px")
    assert base < override, "#toast 基础规则应位于移动端覆盖规则之前"


def test_progress_screen_no_hardcoded_state_words(client):
    """P1-7:进度区 UI 文案走词典(英文界面不出现中文状态词);引擎 log 保留中文。"""
    app_js = client.get("/static/app.js").text
    for gone in ('"✅ 已核查"', '"⚠️ 事实存疑"', "来源（儿童模式已过滤）", "没想出合适的选题",
                 "正在处理…", "素材新节目", "角度：", "首期时长可能偏差较大"):
        assert gone not in app_js, f"进度区文案应走词典而非硬编码: {gone}"
    # 词典确实收了这些键(中英双语,由 test_i18n 保证键齐)
    i18n = client.get("/static/i18n.js").text
    for key in ("state.working", "factcheck.reliable", "topic.empty.hint",
                "calibrate.hint.done", "compliance.title"):
        assert f'"{key}"' in i18n, f"词典缺键: {key}"
