import {studies,runs,plans} from '../api/research.js';
import {store as staged} from '../constants/staged.js';
export const store={studies:[],runs:[],studyId:0,runId:0,rows:[]};
export async function load(){[store.studies,store.runs]=await Promise.all([studies.list(),runs.list()]);if(!store.studyId)store.studyId=store.studies[0]?.id||0;await loadPlans();}
export async function loadRows(){store.rows=store.studyId?await studies.rows(store.studyId):[];}
export async function loadPlans(){staged.plans=await plans.list();if(!staged.planId)staged.planId=staged.plans[0]?.id||0;}
export async function loadPlan(id){staged.planId=Number(id);staged.detail=await plans.get(staged.planId);return staged.detail;}

