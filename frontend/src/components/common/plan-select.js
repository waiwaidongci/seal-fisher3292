import {store as staged} from '../../constants/staged.js';
import {esc} from '../../utils/format.js';
import {store as appStore} from '../../stores/research.js';

export function planSelect(){
 const options=staged.plans.map(p=>`<option value="${p.id}" ${p.id===staged.planId?'selected':''}>#${p.id} ${esc(p.name)} · ${esc(p.status_label)}</option>`).join('');
 return `<label>试封计划<select id="plan-select" name="plan_id">${options||'<option value="0">尚无预登记计划</option>'}</select></label>`;
}
export function planBadge(plan){
 const cls=plan.status==='stopped'?'failed':plan.status==='completed'?'completed':'';
 return `<span class="status ${cls}">${esc(plan.status_label)}</span>`;
}
export function planSummary(){
 if(!staged.plans.length)return '<p class="method-note">尚未预登记分阶段计划。旧的一次性研究不受影响，仍可在“分析执行”与“结果档案”中使用。</p>';
 return `<div class="table-wrap"><table><thead><tr><th>计划</th><th>状态</th><th>已入账阶段</th><th>每组累计</th><th>计划最大误拒</th><th>总限额</th></tr></thead><tbody>${staged.plans.map(p=>`<tr><td><a href="#staged/${p.id}">#${p.id} ${esc(p.name)}</a></td><td>${planBadge(p)}</td><td>${p.looks.length} / ${p.looks_planned}</td><td>${p.looks.map(l=>l.n_per_arm).join(' → ')||'—'}</td><td>${esc(String(p.max_type1))}</td><td>${esc(p.total_alpha)}</td></tr>`).join('')}</tbody></table></div>`;
}
export function studyOptions(){
 return appStore.studies.map(s=>`<option value="${s.id}" ${s.id===appStore.studyId?'selected':''}>${esc(s.name)} · ${s.row_count}条</option>`).join('');
}
