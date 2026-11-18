import {DOMAIN} from '../constants/domain.js';
import {store as staged,STAGED_LIMITS} from '../constants/staged.js';
import {loadPlans,loadPlan} from '../stores/research.js';
import {plans} from '../api/research.js';
import {planBadge,planSelect,planSummary,studyOptions} from '../components/common/plan-select.js';
import {table} from '../components/common/table.js';
import {bindForm} from '../hooks/forms.js';
import {esc,fmt} from '../utils/format.js';

const calibTable=report=>`${table(['计划校验项','结果'],[
 ['共同合格率 p 的最大误拒概率（精确上界）',fmt(report.max_type1_upper_value)],
 ['最大误拒概率（精确下界）',fmt(report.max_type1_lower_value)],
 ['达到最大的共同合格率 p',fmt(report.maximizer_p)],
 ['证书宽度',fmt(report.certified_width)],
 ['总限额 α',report.total_alpha],
 ['各次阈值相加（仅对照，不用于判定）',report.threshold_sum],
 ['是否超过总限额',report.exceeds_limit?'是，拒绝启用':'否，允许启用'],
 ['判定方法',report.method],
])}`;

function lookTable(detail){
 const planned=detail.calibration.looks.map(item=>({
  look:item.look,n:item.n,delta:item.delta,alpha:item.alpha,
  admitted:detail.looks.find(x=>x.look===item.look)||null,
 }));
 return table(['查看','每组累计','本阶段新增','预登记阈值','本次双侧Fisher p','结论依据'],[
  ...planned.map(row=>[
   '第'+row.look+'次',row.n,row.delta,row.alpha,
   row.admitted?row.admitted.fisher_p:'待入账',
   {html:row.admitted?`<span class="status ${row.admitted.decision==='stop'?'failed':'completed'}">${row.admitted.decision==='stop'?'越界停试':'未越界'}</span> ${esc(row.admitted.reason)}`:'—'},
  ]),
 ]);
}

function admitForm(detail,writable){
 if(!writable)return '<p class="method-note">观察员只读。分析师与负责人可入账下一阶段增量。</p>';
 if(!detail.can_admit)return `<div class="method-note"><strong>不再接收增量</strong><p>${esc(detail.stop_reason||'计划已结束')}</p></div>`;
 const n=detail.next_delta;
 return `<form id="admit-form" class="worksheet"><h2>第 ${detail.next_look} 次查看入账</h2><p>本次每组新增 ${n} 件，两组必须同时入账；累计每组将达 ${detail.next_n} 件。重复提交同一幂等键只回放已有结论，不会重复推进。</p><div class="inline-form"><label>A组合格数<input name="arm_a_success" type="number" min="0" max="${n}" value="0" required></label><label>A组失败数<input name="arm_a_failure" type="number" min="0" max="${n}" value="${n}" required></label><label>B组合格数<input name="arm_b_success" type="number" min="0" max="${n}" value="0" required></label><label>B组失败数<input name="arm_b_failure" type="number" min="0" max="${n}" value="${n}" required></label></div><label>幂等键（至少8位，可重试）<input name="idempotency_key" minlength="8" maxlength="120" required value="look-${detail.id}-${detail.next_look}-${Date.now()}"></label><button type="submit">两组同时入账并判定 →</button></form>`;
}

function buildForm(editing){
 const looks=editing?editing.calibration.looks:[];
 const value=(index,key)=>looks[index]?esc(looks[index][key]):'';
 const rows=[0,1,2].map(i=>`<div class="inline-form"><label>第${i+1}次 每组累计样本量<input name="n_${i+1}" type="number" min="1" max="${STAGED_LIMITS.maxGroup}" value="${value(i,'n')}" placeholder="${i?'留空表示不设本次':'必填'}"></label><label>第${i+1}次 双侧Fisher阈值<input name="alpha_${i+1}" value="${value(i,'alpha')}" placeholder="${i?'留空表示不设本次':'必填'}"></label></div>`).join('');
 const total=editing?editing.calibration.total_alpha:'0.05';
 const name=editing?editing.name:'分阶段试封 · '+new Date().toISOString().slice(0,10);
 const options=studyOptions().replace('value="'+editing?.study_id+'"','value="'+editing?.study_id+'" selected');
 return `<form id="plan-form" class="worksheet"><h2>${editing?'修改预登记计划 #'+editing.id:'预登记分阶段计划'}</h2><p>每组至多 ${STAGED_LIMITS.maxGroup} 件、至多 ${STAGED_LIMITS.maxLooks} 次查看；累计样本量须严格递增，各次阈值在0到1之间。首批入账后计划锁定。启用前精确计算共同合格率在0到1间的最大误拒概率，超过总限额即拒绝。</p><label>所属${DOMAIN.study_label}<select name="study_id">${options}</select></label><label>计划名称<input name="name" required minlength="2" maxlength="120" value="${esc(name)}"></label>${rows}<label>总限额 α<input name="total_alpha" value="${esc(total)}" required></label><div class="toolbar"><button type="submit">${editing?'重新校验并保存':'校验并启用计划'} →</button><button type="button" class="secondary" id="preview-button">只做计划校验</button></div></form><div id="preview-result"></div>`;
}

export async function render(id){
 await loadPlans();
 const writable=localStorage.getItem('research-role')!=='viewer';
 if(!id&&!staged.plans.length)staged.detail=null;
 else staged.detail=await loadPlan(id||staged.planId);
 const detail=staged.detail;
 const detailBlock=detail?`<h2>#${detail.id} ${esc(detail.name)} ${planBadge(detail)}</h2>
 <p>每组累计计划 ${detail.calibration.looks.map(l=>l.n).join(' → ')} 件；阈值 ${detail.calibration.looks.map(l=>l.alpha).join(' → ')}；总限额 ${esc(detail.calibration.total_alpha)}。${detail.locked?'计划已锁定：'+esc(detail.locked_at):'首批入账前可修改。'}</p>
 ${writable&&detail.can_edit?buildForm(detail):''}
 ${detail.status==='stopped'?`<div class="error"><strong>停试原因</strong><p>${esc(detail.stop_reason)}</p><p>停试结论永久保留；恢复、重试或其他阶段都不能推翻。</p></div>`:''}
 <h2>计划校验</h2>${calibTable(detail.calibration)}
 <h2>阶段依据</h2>${lookTable(detail)}${admitForm(detail,writable)}`
 :'';
 return `<div class="eyebrow">06 / STAGED SEAL TRIAL</div><h1>分阶段试封与停试</h1>
 <p class="lead">按预登记的累计样本量与各次阈值逐次查看；每组至多${STAGED_LIMITS.maxGroup}件、至多${STAGED_LIMITS.maxLooks}次。首次越界永久停试，否则到末次结束。旧的一次性Fisher研究完整保留。</p>
 <div class="method-note">每阶段按双侧Fisher对累计2×2表判定。仅接收下一阶段增量，A/B两组同时入账；重复或并发提交不重复推进。计划校验使用精确有理多项式与Sturm判定，不用网格抽样，也不用各次阈值相加代替。</div>
 ${writable&&!(detail&&detail.can_edit)?buildForm(null):''}
 <h2>已有计划</h2>${staged.plans.length>1?`<div class="toolbar">${planSelect()}</div>`:''}${planSummary()}
 ${detail?`<h2>计划详情</h2>${detailBlock}`:''}`;
}

function readLooks(form){
 const rows=[1,2,3].map(i=>({n:form.elements['n_'+i]?.value,alpha:form.elements['alpha_'+i]?.value}));
 while(rows.length&&!rows.at(-1).n&&!rows.at(-1).alpha)rows.pop();
 if(rows.some(row=>!row.n||!row.alpha))throw new Error('阶段必须从第1次起连续填写累计样本量与阈值');
 return rows.map(row=>({n:Number(row.n),alpha:String(row.alpha)}));
}
function payload(form){
 return {study_id:Number(form.elements.study_id.value),name:form.elements.name.value,looks:readLooks(form),total_alpha:String(form.elements.total_alpha.value)};
}
function showPreview(report){
 const box=document.getElementById('preview-result');
 if(!box)return;
 box.innerHTML=`<div class="method-note"><strong>计划校验${report.exceeds_limit?'未通过':'通过'}</strong></div>${calibTable(report)}`;
}

export function bind(refresh){
 const form=document.getElementById('plan-form');
 const previewButton=document.getElementById('preview-button');
 if(previewButton&&form)previewButton.onclick=async()=>{
  try{showPreview(await plans.preview(payload(form)));}catch(e){document.getElementById('notice').textContent=e.message;}
 };
 bindForm('plan-form',async data=>{
  const p=payload(form);
  const existing=staged.detail&&staged.detail.can_edit?staged.detail:null;
  const saved=existing?await plans.update(existing.id,p):await plans.create(p);
  staged.planId=saved.id;
  await loadPlans();
  if(location.hash!=='#staged/'+saved.id)location.hash='staged/'+saved.id;
  else refresh();
 });
 bindForm('admit-form',async data=>{
  const detail=staged.detail;
  const body={look:detail.next_look,arm_a_success:+data.arm_a_success,arm_a_failure:+data.arm_a_failure,arm_b_success:+data.arm_b_success,arm_b_failure:+data.arm_b_failure,idempotency_key:data.idempotency_key};
  const updated=await plans.admit(detail.id,body);
  staged.detail=updated;
  await loadPlans();
  const last=updated.looks[updated.looks.length-1];
  document.getElementById('notice').textContent=updated.stop_reason||(last?last.reason:'');
  refresh();
 });
 const select=document.getElementById('plan-select');
 if(select)select.onchange=()=>{location.hash='staged/'+select.value;};
}
