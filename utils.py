from sqlalchemy.orm import Session
from db import SessionLocal, Provider, APIKey, ModelMapping, UsageLog, Base, ToolDefaultModel, SUPPORTED_PROVIDER_TYPES
import datetime
import requests
import os
import json
import socket


def delete_item(model_class, item_id, success_msg):
    # streamlit 懒加载：本模块同时被 FastAPI 后端引用，不能在顶层 import streamlit
    import streamlit as st

    with SessionLocal() as session:
        try:
            if model_class == Provider:
                key_ids = [k.id for k in session.query(APIKey).filter(APIKey.provider_id == item_id).all()]
                if key_ids:
                    session.query(UsageLog).filter(UsageLog.key_id.in_(key_ids)).delete(synchronize_session=False)
                session.query(APIKey).filter(APIKey.provider_id == item_id).delete()
                session.query(ModelMapping).filter(ModelMapping.provider_id == item_id).delete()
            
            session.query(model_class).filter(model_class.id == item_id).delete()
            session.commit()
            st.toast(success_msg, icon="✅")
            return True
        except Exception as e:
            session.rollback()
            st.error(f"删除失败: {e}")
            return False


def get_ordered_model_mappings():
    """读取并规范化模型映射顺序（1..N 连续），返回 (ModelMapping, Provider) 列表。"""
    with SessionLocal() as session:
        mappings = (
            session.query(ModelMapping, Provider)
            .join(Provider)
            .order_by(ModelMapping.order, ModelMapping.id)
            .all()
        )
        changed = False
        for idx, (m, _) in enumerate(mappings, start=1):
            if m.order != idx:
                m.order = idx
                changed = True
        if changed:
            session.commit()
            mappings = (
                session.query(ModelMapping, Provider)
                .join(Provider)
                .order_by(ModelMapping.order, ModelMapping.id)
                .all()
            )
        return mappings


def move_model_mapping(model_id: int, direction: str) -> bool:
    """上移/下移某个模型映射。direction: up | down"""
    with SessionLocal() as session:
        mappings = session.query(ModelMapping).order_by(ModelMapping.order, ModelMapping.id).all()
        ids = [m.id for m in mappings]
        if model_id not in ids:
            return False

        idx = ids.index(model_id)
        target_idx = idx - 1 if direction == "up" else idx + 1
        if target_idx < 0 or target_idx >= len(mappings):
            return False

        current = mappings[idx]
        target = mappings[target_idx]
        current.order, target.order = target.order, current.order
        session.commit()
        return True


def apply_model_order(ordered_ids: list[int]) -> bool:
    """按给定 ID 顺序更新模型映射 order。"""
    if not ordered_ids:
        return False
    with SessionLocal() as session:
        models = session.query(ModelMapping).filter(ModelMapping.id.in_(ordered_ids)).all()
        by_id = {m.id: m for m in models}
        for idx, model_id in enumerate(ordered_ids, start=1):
            model = by_id.get(model_id)
            if model is not None:
                model.order = idx
        session.commit()
    return True


def upsert_env_value(key: str, value: str, env_path: str = ".env") -> None:
    """更新 .env 中的某个 key（不存在则追加）"""
    existing_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            existing_lines = f.read().splitlines()

    updated = []
    found = False
    for line in existing_lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            updated.append(line)
            continue
        k, _ = line.split("=", 1)
        if k.strip() == key:
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)

    if not found:
        updated.append(f"{key}={value}")

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated).rstrip() + "\n")


def copy_button(label: str, value: str, key: str):
    import streamlit as st

    safe_value = json.dumps(value)
    safe_label = json.dumps(label)
    html = f"""
    <button id="{key}" style="padding:0.35rem 0.7rem;border:1px solid #bbb;border-radius:6px;cursor:pointer;">
      {label}
    </button>
    <script>
      const btn = document.getElementById({json.dumps(key)});
      if (btn) {{
        btn.onclick = async () => {{
          try {{
            await navigator.clipboard.writeText({safe_value});
            btn.innerText = "已复制";
            setTimeout(() => btn.innerText = {safe_label}, 1200);
          }} catch (e) {{
            btn.innerText = "复制失败";
            setTimeout(() => btn.innerText = {safe_label}, 1200);
          }}
        }};
      }}
    </script>
    """
    st.iframe(html, height=40)


def fetch_models(api_base, api_key):
    try:
        url = api_base.rstrip("/")
        if not url.endswith("/models"):
            url = f"{url}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                models = []
                for m in data["data"]:
                    if isinstance(m, dict):
                        model_id = m.get("id") or m.get("model") or m.get("name")
                        if model_id:
                            models.append(model_id)
                    elif isinstance(m, str):
                        models.append(m)
                return models
            elif isinstance(data, list):
                models = []
                for m in data:
                    if isinstance(m, dict):
                        model_id = m.get("id") or m.get("model") or m.get("name")
                        if model_id:
                            models.append(model_id)
                    elif isinstance(m, str):
                        models.append(m)
                return models
        return []
    except Exception as e:
        return []


def test_upstream_connectivity(api_base, api_key, test_model=None):
    clean_base = (api_base or "").strip().strip("`").strip("'").strip("\"").rstrip("/")
    clean_key = (api_key or "").strip().strip("`").strip("'").strip("\"")
    if not clean_base or not clean_key:
        return False, "配置不完整：缺少 API Base 或 Key"
    url = f"{clean_base}/models"
    headers = {"Authorization": f"Bearer {clean_key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        status = resp.status_code
        if status == 200:
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                models = [m.get("id", "") for m in data["data"] if isinstance(m, dict)]
                model_count = len(models)
                if test_model:
                    if test_model in models:
                        return True, f"连通成功：HTTP 200，可用模型数 {model_count}，已找到映射模型 {test_model}"
                    return False, f"连通成功但模型不匹配：未在 /models 中找到 {test_model}（共 {model_count} 个模型）"
                return True, f"连通成功：HTTP 200，可用模型数 {model_count}"
            return True, "连通成功：HTTP 200"
        if status in (401, 403):
            return False, f"鉴权失败：HTTP {status}，请检查上游 Key 权限"
        if status == 404:
            return False, "接口路径异常：HTTP 404，请检查 API Base 是否正确（通常应包含 /v1）"
        body = resp.text[:200].replace("\n", " ")
        return False, f"连通失败：HTTP {status}，响应：{body}"
    except Exception as e:
        return False, f"请求异常：{str(e)}"


def test_upstream_chat(api_base, api_key, model_name):
    clean_base = (api_base or "").strip().strip("`").strip("'").strip("\"").rstrip("/")
    clean_key = (api_key or "").strip().strip("`").strip("'").strip("\"")
    clean_model = (model_name or "").strip().strip("`").strip("'").strip("\"")
    if not clean_base or not clean_key or not clean_model:
        return False, "配置不完整：缺少 API Base、Key 或模型名"
    url = f"{clean_base}/chat/completions"
    headers = {"Authorization": f"Bearer {clean_key}", "Content-Type": "application/json"}
    payload = {
        "model": clean_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        status = resp.status_code
        if status == 200:
            return True, f"对话测试成功：HTTP 200，模型 {clean_model} 可调用"
        body = resp.text[:220].replace("\n", " ")
        if status in (401, 403):
            return False, f"对话鉴权失败：HTTP {status}，模型 {clean_model}，响应：{body}"
        return False, f"对话失败：HTTP {status}，模型 {clean_model}，响应：{body}"
    except Exception as e:
        return False, f"对话请求异常：{str(e)}"


def classify_model_type(model_name: str) -> str:
    """粗略区分模型类型：image / text"""
    lower = (model_name or "").lower()
    image_keywords = [
        "stable-diffusion",
        "stable_diffusion",
        "stable diffusion",
        "sd3",
        "sd-3",
        "sd 3",
        "sd3.5",
        "sd-3.5",
        "sd 3.5",
        "sdxl",
        "diffusion",
        "flux",
        "imagen",
        "dall-e",
        "image",
        "stabilityai/",
        "black-forest-labs/",
        "playgroundai/",
    ]
    if any(k in lower for k in image_keywords):
        return "image"
    if "stable" in lower and "diffusion" in lower:
        return "image"
    return "text"


def log_custom_usage(
    key_id: int,
    model_name: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    images_count: int = 0,
):
    """把模型体验页调用写入 usage 统计"""
    with SessionLocal() as session:
        log = UsageLog(
            key_id=key_id,
            model_name=model_name,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            total_tokens=total_tokens or ((prompt_tokens or 0) + (completion_tokens or 0)),
            images_count=images_count or 0,
        )
        session.add(log)
        session.query(APIKey).filter(APIKey.id == key_id).update(
            {"usage_count": APIKey.usage_count + 1},
            synchronize_session=False,
        )
        session.commit()


def detect_proxy_base_url() -> tuple[str, list[str]]:
    """推断代理可访问地址，并返回候选列表"""
    proxy_port = (os.getenv("PROXY_PORT", "8000") or "8000").strip()
    proxy_host = (os.getenv("PROXY_HOST", "0.0.0.0") or "0.0.0.0").strip()
    explicit = (os.getenv("PROXY_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

    candidates = []
    if explicit:
        candidates.append(explicit)

    if proxy_host and proxy_host not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
        candidates.append(f"http://{proxy_host}:{proxy_port}")

    candidates.append(f"http://localhost:{proxy_port}")
    candidates.append(f"http://127.0.0.1:{proxy_port}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            if lan_ip and lan_ip not in {"127.0.0.1", "0.0.0.0"}:
                candidates.append(f"http://{lan_ip}:{proxy_port}")
    except Exception:
        pass

    uniq = []
    for item in candidates:
        normalized = (item or "").strip().rstrip("/")
        if normalized and normalized not in uniq:
            uniq.append(normalized)

    default_base = uniq[0] if uniq else f"http://localhost:{proxy_port}"
    return default_base, uniq
