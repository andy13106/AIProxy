import streamlit as st
from sqlalchemy import func
from db import SessionLocal, UsageLog
import datetime


def render_overview_page():
    st.header("📊 使用统计概览")
    today = datetime.date.today()
    today_start = datetime.datetime.combine(today, datetime.time.min)
    today_end = datetime.datetime.combine(today, datetime.time.max)
    page_size = 20

    with SessionLocal() as session:
        session.query(UsageLog).filter(
            UsageLog.prompt_tokens == 0,
            UsageLog.completion_tokens == 0,
            UsageLog.total_tokens == 0,
            UsageLog.images_count == 0
        ).delete()
        session.commit()

        today_summary = session.query(
            UsageLog.model_name,
            func.count(UsageLog.id).label("request_count"),
            func.sum(UsageLog.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageLog.completion_tokens).label("completion_tokens"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.images_count).label("images_count"),
        ).filter(
            UsageLog.timestamp >= today_start,
            UsageLog.timestamp <= today_end
        ).group_by(UsageLog.model_name).order_by(func.sum(UsageLog.total_tokens).desc()).all()

        all_time_summary = session.query(
            UsageLog.model_name,
            func.count(UsageLog.id).label("request_count"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.images_count).label("images_count"),
        ).group_by(UsageLog.model_name).order_by(func.sum(UsageLog.total_tokens).desc()).all()

        all_models = [r[0] for r in session.query(UsageLog.model_name).distinct().order_by(UsageLog.model_name).all()]

    if today_summary:
        total_requests_today = sum(row.request_count or 0 for row in today_summary)
        total_tokens_today = sum(row.total_tokens or 0 for row in today_summary)
        total_images_today = sum(row.images_count or 0 for row in today_summary)

        c1, c2, c3 = st.columns(3)
        c1.metric("今日总消耗 Tokens", f"{total_tokens_today:,}")
        c2.metric("今日请求次数", f"{total_requests_today:,}")
        c3.metric("今日生成图片", f"{total_images_today:,}")

        st.subheader("今日按模型汇总")
        summary_df = [{
            "模型": row.model_name,
            "请求次数": row.request_count or 0,
            "Prompt Tokens": row.prompt_tokens or 0,
            "Completion Tokens": row.completion_tokens or 0,
            "Total Tokens": row.total_tokens or 0,
            "图片数": row.images_count or 0,
        } for row in today_summary]
        st.dataframe(summary_df, width="stretch", hide_index=True)
    else:
        st.info("今天还没有有效使用记录。")

    st.divider()
    st.subheader("模型数据管理（全历史）")
    st.caption("这里列出所有历史出现过的模型，点击操作列可删除该模型的所有统计数据。下方明细Tab会同步更新。")

    if all_time_summary:
        h_model, h_req, h_tokens, h_images, h_action = st.columns([4, 2, 2, 2, 1])
        h_model.write("**模型**")
        h_req.write("**历史请求次数**")
        h_tokens.write("**历史总 Tokens**")
        h_images.write("**历史图片数**")
        h_action.write("**操作**")
        st.divider()

        for row in all_time_summary:
            c_model, c_req, c_tokens, c_images, c_action = st.columns([4, 2, 2, 2, 1])
            c_model.write(f"**{row.model_name}**")
            c_req.write(str(row.request_count or 0))
            c_tokens.write(f"{row.total_tokens or 0:,}")
            c_images.write(str(row.images_count or 0))
            if c_action.button("🗑️", key=f"usage_delete_{row.model_name}", help=f"删除 {row.model_name} 的所有统计数据"):
                with SessionLocal() as session:
                    deleted = session.query(UsageLog).filter(UsageLog.model_name == row.model_name).delete()
                    session.commit()
                st.toast(f"已删除模型 {row.model_name} 的 {deleted} 条统计记录", icon="🧹")
                st.rerun()
    else:
        st.info("暂无可管理的历史模型数据。")

    st.divider()
    st.subheader("明细查询（按模型Tab）")

    if all_models:
        tab_names = [f"📊 {m}" for m in all_models]
        tabs = st.tabs(tab_names)

        for tab_idx, (tab, model_name) in enumerate(zip(tabs, all_models)):
            with tab:
                st.caption(f"模型：{model_name}")

                d1, d2 = st.columns(2)
                start_date = d1.date_input(
                    "开始日期",
                    value=today,
                    key=f"start_date_{model_name}"
                )
                end_date = d2.date_input(
                    "结束日期",
                    value=today,
                    key=f"end_date_{model_name}"
                )

                page_key = f"usage_page_{model_name}"
                if page_key not in st.session_state:
                    st.session_state[page_key] = 1

                detail_start = datetime.datetime.combine(start_date, datetime.time.min)
                detail_end = datetime.datetime.combine(end_date, datetime.time.max)

                with SessionLocal() as session:
                    detail_query = session.query(UsageLog).filter(
                        UsageLog.model_name == model_name,
                        UsageLog.timestamp >= detail_start,
                        UsageLog.timestamp <= detail_end
                    ).order_by(UsageLog.timestamp.desc())

                    total_detail_count = detail_query.count()
                    total_pages = max(1, (total_detail_count + page_size - 1) // page_size)

                    if st.session_state[page_key] > total_pages:
                        st.session_state[page_key] = total_pages

                    detail_logs = detail_query.offset((st.session_state[page_key] - 1) * page_size).limit(page_size).all()

                    totals = session.query(
                        func.count(UsageLog.id),
                        func.sum(UsageLog.prompt_tokens),
                        func.sum(UsageLog.completion_tokens),
                        func.sum(UsageLog.total_tokens),
                        func.sum(UsageLog.images_count),
                    ).filter(
                        UsageLog.model_name == model_name,
                        UsageLog.timestamp >= detail_start,
                        UsageLog.timestamp <= detail_end
                    ).first()

                c1, c2, c3 = st.columns(3)
                c1.metric("范围内总 Tokens", f"{(totals[3] or 0):,}")
                c2.metric("范围内请求数", f"{(totals[0] or 0):,}")
                c3.metric("范围内图片数", f"{(totals[4] or 0):,}")

                if detail_logs:
                    detail_df = [{
                        "ID": log.id,
                        "Prompt": log.prompt_tokens,
                        "Completion": log.completion_tokens,
                        "Total": log.total_tokens,
                        "图片": log.images_count,
                        "时间": log.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                    } for log in detail_logs]
                    st.dataframe(detail_df, width="stretch", hide_index=True)

                    p1, p2, p3 = st.columns([1, 2, 1])
                    if p1.button("上一页", key=f"prev_{model_name}", disabled=st.session_state[page_key] <= 1, width="stretch"):
                        st.session_state[page_key] -= 1
                        st.rerun()
                    p2.markdown(f"<div style='text-align:center;padding-top:8px;'>第 {st.session_state[page_key]} / {total_pages} 页</div>", unsafe_allow_html=True)
                    if p3.button("下一页", key=f"next_{model_name}", disabled=st.session_state[page_key] >= total_pages, width="stretch"):
                        st.session_state[page_key] += 1
                        st.rerun()
                else:
                    st.info(f"该日期范围内没有查到 {model_name} 的明细记录。")
    else:
        st.info("暂无可查询的模型明细。")
