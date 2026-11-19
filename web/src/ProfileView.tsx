import { useEffect, useRef, useState } from 'react'
import { request } from './api'
import type { BackgroundTask } from './types'

type FeatureResult = { value: string; certainty: 'clear' | 'uncertain'; evidence: string }
type FeatureDefinition = { label: string; group: string; options: Record<string, string> }
type FeatureSchema = { groups: Record<string, { label: string; technique: string }>; features: Record<string, FeatureDefinition> }
type ProfilePhoto = {
  version?: string; image_url?: string; base_url?: string; base_error?: string
  observations?: { features?: Record<string, FeatureResult> } | null
  analysis_task?: BackgroundTask; base_task?: BackgroundTask
}
type Profile = {
  skill_level: string; skin_tendency: string | null; preferences: string[]
  sensitivities: string[]; available_tools: string[] | null
  face_feature_choices: Record<string, string>; face_feature_decisions: Record<string, string>
  face_feature_photo_version: string | null
}

const working = (task?: BackgroundTask) => task?.status === 'queued' || task?.status === 'running'
const splitEntries = (value: string) => value.split(/[，,、\n]/).map(item => item.trim()).filter(Boolean)
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error)

export default function ProfileView() {
  const [photo, setPhoto] = useState<ProfilePhoto>({})
  const [profile, setProfile] = useState<Profile | null>(null)
  const [schema, setSchema] = useState<FeatureSchema | null>(null)
  const [loadingError, setLoadingError] = useState('')
  const [photoBusy, setPhotoBusy] = useState(false)
  const [showUploadHint, setShowUploadHint] = useState(false)
  const photoInputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const input = photoInputRef.current
    if (!input) return
    const cancelled = () => setShowUploadHint(false)
    input.addEventListener('cancel', cancelled)
    return () => input.removeEventListener('cancel', cancelled)
  }, [profile])
  const [uploadStatus, setUploadStatus] = useState('')
  const [analysisStatus, setAnalysisStatus] = useState('')
  const [baseStatus, setBaseStatus] = useState('')
  const [fieldStatus, setFieldStatus] = useState<Record<string, string>>({})
  const [groupStatus, setGroupStatus] = useState<Record<string, string>>({})
  const [editingGroup, setEditingGroup] = useState<string | null>(null)
  const [draftChoices, setDraftChoices] = useState<Record<string, string>>({})
  const [listDraft, setListDraft] = useState<Record<string, string>>({})
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteStatus, setDeleteStatus] = useState('')

  useEffect(() => {
    Promise.all([
      request<ProfilePhoto>('/api/profile/photo'),
      request<Profile>('/api/profile'),
      request<FeatureSchema>('/api/profile/feature-options'),
    ]).then(([nextPhoto, nextProfile, nextSchema]) => {
      setPhoto(nextPhoto); setProfile(nextProfile); setSchema(nextSchema)
      setListDraft({
        preferences: (nextProfile.preferences || []).join('、'),
        sensitivities: (nextProfile.sensitivities || []).join('、'),
        available_tools: (nextProfile.available_tools || []).join('、'),
      })
    }).catch(error => setLoadingError(errorText(error)))
  }, [])

  useEffect(() => {
    if (!photo.image_url || ![photo.analysis_task, photo.base_task].some(working)) return
    const timer = window.setInterval(() => {
      request<ProfilePhoto>('/api/profile/photo').then(setPhoto).catch(() => {})
    }, 3000)
    return () => window.clearInterval(timer)
  }, [photo.image_url, photo.analysis_task?.status, photo.base_task?.status])

  const saveField = async (field: string, value: string | string[] | null) => {
    if (!profile) return
    const previous = profile[field as keyof Profile]
    setProfile({ ...profile, [field]: value })
    setFieldStatus(current => ({ ...current, [field]: '保存中…' }))
    try {
      const saved = await request<Profile>('/api/profile', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      })
      setProfile(current => current ? { ...current, [field]: saved[field as keyof Profile] } : saved)
      setFieldStatus(current => ({ ...current, [field]: '已保存' }))
      window.dispatchEvent(new Event('profile-saved'))
    } catch (error) {
      setProfile(current => current ? { ...current, [field]: previous } : current)
      setFieldStatus(current => ({ ...current, [field]: errorText(error) }))
    }
  }

  const saveList = (field: 'preferences' | 'sensitivities' | 'available_tools') => {
    const parsed = splitEntries(listDraft[field] || '')
    const old = profile?.[field] || []
    if (JSON.stringify(parsed) !== JSON.stringify(old)) void saveField(field, field === 'available_tools' && !parsed.length ? null : parsed)
  }

  const uploadPhoto = async (file?: File) => {
    if (!file) { setShowUploadHint(false); return }
    setPhotoBusy(true); setShowUploadHint(false); setUploadStatus('上传中，正在准备新底图…')
    try {
      const form = new FormData()
      form.append('image', file)
      const nextPhoto = await request<ProfilePhoto>('/api/profile/photo', { method: 'POST', body: form })
      setPhoto(nextPhoto)
      setEditingGroup(null)
      setUploadStatus('上传成功，正在分别处理面部分析和标准底图')
    } catch (error) { setUploadStatus(errorText(error)) }
    finally { setPhotoBusy(false) }
  }

  const retry = async (kind: 'profile_analysis' | 'base_image') => {
    const setter = kind === 'profile_analysis' ? setAnalysisStatus : setBaseStatus
    setter('正在重新处理…')
    try {
      await request(`/api/profile/photo/${kind}/retry`, { method: 'POST' })
      setPhoto(await request<ProfilePhoto>('/api/profile/photo'))
      setter('任务已开始')
    } catch (error) { setter(errorText(error)) }
  }

  const deletePhoto = async () => {
    setUploadStatus('正在删除…')
    try {
      await request('/api/profile/photo', { method: 'DELETE' })
      setPhoto({}); setEditingGroup(null)
      setProfile(await request<Profile>('/api/profile'))
      setUploadStatus('照片及关联图片已删除')
      window.dispatchEvent(new Event('profile-saved'))
    } catch (error) { setUploadStatus(errorText(error)) }
  }

  const saveChoices = async (group: string, decision: 'confirmed' | 'corrected', choices: Record<string, string>) => {
    setGroupStatus(current => ({ ...current, [group]: '保存中…' }))
    try {
      const saved = await request<Profile>('/api/profile/features', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ photo_version: photo.version, choices, decision }),
      })
      setProfile(current => current ? { ...current,
        face_feature_choices: saved.face_feature_choices,
        face_feature_decisions: saved.face_feature_decisions,
        face_feature_photo_version: saved.face_feature_photo_version,
      } : saved); setEditingGroup(null)
      setGroupStatus(current => ({ ...current, [group]: decision === 'confirmed' ? '已确认，生成教程时会使用' : '已修正，生成教程时会使用' }))
      window.dispatchEvent(new Event('profile-saved'))
    } catch (error) { setGroupStatus(current => ({ ...current, [group]: errorText(error) })) }
  }

  const startEditing = (group: string, keys: string[]) => {
    const choices: Record<string, string> = {}
    keys.forEach(key => { choices[key] = selectedValue(key) })
    setDraftChoices(choices); setEditingGroup(group)
  }

  const selectedValue = (key: string) => {
    if (profile?.face_feature_photo_version === photo.version && profile?.face_feature_choices?.[key]) return profile.face_feature_choices[key]
    return photo.observations?.features?.[key]?.value || 'unknown'
  }

  const featureStatus = (key: string) => {
    if (profile && profile.face_feature_photo_version === photo.version && profile.face_feature_decisions?.[key]) {
      return profile.face_feature_decisions[key] === 'corrected' ? '你已修正' : '你已确认'
    }
    const observed = photo.observations?.features?.[key]
    return observed?.certainty === 'clear' ? 'AI 识别' : '待确认'
  }

  const deleteProfile = async () => {
    setDeleteBusy(true); setDeleteStatus('正在删除…')
    try {
      await request('/api/profile', { method: 'DELETE' })
      const fresh = await request<Profile>('/api/profile')
      setProfile(fresh); setPhoto({}); setEditingGroup(null)
      setListDraft({ preferences: '', sensitivities: '', available_tools: '' })
      setDeleteStatus('资料已删除')
      window.dispatchEvent(new Event('profile-saved'))
    } catch (error) { setDeleteStatus(errorText(error)) }
    finally { setDeleteBusy(false) }
  }

  const hasAnalysis = Boolean(photo.observations?.features)
  const oldAnalysis = Boolean(photo.observations && !hasAnalysis)
  const analysisError = photo.analysis_task?.error
  const baseError = photo.base_task?.error || photo.base_error

  return <section className="profile-page profile-simple page-enter">
    <header className="profile-simple-heading"><span className="eyebrow">YOUR BEAUTY PROFILE</span><h1>我的资料</h1><p>上传一张素颜照，确认面部特点；其余只填会影响教程的偏好。</p></header>
    {loadingError && <p className="profile-inline-status" role="alert">资料加载失败：{loadingError}</p>}
    {!profile || !schema ? <p className="profile-loading">正在读取资料…</p> : <div className="profile-simple-content">
      <section className="profile-card" aria-labelledby="profile-photo-title">
        <div className="profile-simple-title"><h2 id="profile-photo-title">我的素颜照</h2><p>原图用于分析；标准底图用于教程图片。</p></div>
        <div className="profile-standard-image profile-base-primary">
          {photoBusy ? <p role="status">正在处理新照片，标准底图稍后显示…</p> : photo.base_url ? <img src={photo.base_url} alt="AI 生成的标准素颜底图" /> : <p role="status">{working(photo.base_task) ? '正在生成标准底图…' : baseError || (photo.image_url ? '等待生成标准底图' : '上传素颜照后生成标准底图')}</p>}
          {!photoBusy && photo.image_url && (baseError || (!photo.base_url && !working(photo.base_task))) && <button type="button" onClick={() => void retry('base_image')}>重试底图</button>}
          {photo.base_url && <small>AI 效果示意</small>}
          {baseStatus && <p className="profile-inline-status" role="status">{baseStatus}</p>}
        </div>
        <div className="profile-photo-controls profile-photo-actions">
          <label className="profile-photo-upload" onClick={() => setShowUploadHint(true)}><strong>{photo.image_url ? '更换照片' : '选择照片'}</strong><input ref={photoInputRef} className="sr-only" type="file" disabled={photoBusy} accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" onChange={event => { void uploadPhoto(event.target.files?.[0]); event.target.value = '' }} /></label>
          {photo.image_url && <button type="button" className="profile-text-button" onClick={() => void deletePhoto()}>删除照片</button>}
        </div>
        {showUploadHint && !photoBusy && <small className="profile-upload-hint">支持 JPG、PNG、WEBP，最多 12 MB</small>}
        {uploadStatus && <p className="profile-inline-status" role="status">{uploadStatus}</p>}
        {photo.image_url && <details className="profile-base-details"><summary>查看原始素颜照</summary><img className="profile-source-photo" src={photo.image_url} alt="当前原始素颜照" /></details>}
      </section>

      <section className="profile-card" aria-labelledby="profile-features-title">
        <div className="profile-simple-title"><h2 id="profile-features-title">我的面部特点</h2><p>只保留能帮助确定化妆位置和手法的观察；你选的结果优先于 AI。</p></div>
        {!photo.image_url ? <p className="profile-empty-hint">上传素颜照后会在这里看到分析。</p> : !hasAnalysis ? <div className="profile-analysis-progress">
          <p className="profile-inline-status" role="status">{working(photo.analysis_task) ? '正在分析可见特点…' : analysisError || (oldAnalysis ? '已有旧版分析，请重新分析以获得可选结果。' : '等待面部分析')}</p>
          {!working(photo.analysis_task) && <button type="button" onClick={() => void retry('profile_analysis')}>重新分析</button>}
          {analysisStatus && <p className="profile-inline-status" role="status">{analysisStatus}</p>}
        </div> : <>
          <div className="profile-feature-grid">
            {Object.entries(schema.groups).map(([group, detail]) => {
              const keys = Object.keys(schema.features).filter(key => schema.features[key].group === group)
              const observed = photo.observations?.features || {}
              const canConfirm = keys.some(key => observed[key]?.value && observed[key].value !== 'unknown') &&
                !(profile?.face_feature_photo_version === photo.version && keys.some(key => profile.face_feature_decisions?.[key] === 'corrected'))
              return <article className="profile-feature-card" key={group}>
                <div className="profile-feature-heading"><h3>{detail.label}</h3><small>{detail.technique}</small></div>
                {editingGroup === group ? <>
                  <div className="profile-feature-list">{keys.map(key => <label key={key}>{schema.features[key].label}<select value={draftChoices[key] || 'unknown'} onChange={event => setDraftChoices(current => ({ ...current, [key]: event.target.value }))}>{Object.entries(schema.features[key].options).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}</div>
                  <div className="profile-feature-actions"><button type="button" onClick={() => void saveChoices(group, 'corrected', draftChoices)}>保存修正</button><button type="button" onClick={() => setEditingGroup(null)}>取消</button></div>
                </> : <>
                  <dl className="profile-feature-list">{keys.map(key => <div key={key}><dt>{schema.features[key].label}</dt><dd>{schema.features[key].options[selectedValue(key)] || '无法判断'}<small>{featureStatus(key)}</small></dd></div>)}</dl>
                  <div className="profile-feature-actions">{canConfirm && <button type="button" onClick={() => void saveChoices(group, 'confirmed', Object.fromEntries(keys.map(key => [key, observed[key]?.value || 'unknown'])))}>确认结果</button>}<button type="button" onClick={() => startEditing(group, keys)}>修正选项</button></div>
                </>}
                {groupStatus[group] && <p className="profile-inline-status" role="status">{groupStatus[group]}</p>}
                {keys.some(key => observed[key]?.evidence) && <details className="profile-evidence"><summary>查看判断依据</summary>{keys.map(key => observed[key]?.evidence && <p key={key}>{schema.features[key].label}：{observed[key].evidence}</p>)}</details>}
              </article>
            })}
          </div>
          <p className="profile-fine-print">光线和角度会影响识别。局部暗沉、泛红与阴影只表示照片中可见的明暗，不代表诊断。</p>
          {analysisError && <p className="profile-inline-status" role="status">最近一次重新分析失败：{analysisError} <button type="button" onClick={() => void retry('profile_analysis')}>重试</button></p>}
        </>}
      </section>

      <section className="profile-card" aria-labelledby="profile-choices-title">
        <div className="profile-simple-title"><h2 id="profile-choices-title">我的偏好</h2><p>由你填写，照片分析不会覆盖。</p></div>
        <div className="profile-choice-grid">
          <label>化妆熟练度<select value={profile.skill_level} onChange={event => void saveField('skill_level', event.target.value)}><option value="beginner">新手</option><option value="intermediate">熟练</option><option value="advanced">进阶</option></select><small role="status">{fieldStatus.skill_level}</small></label>
          <label>肤质感受<select value={profile.skin_tendency || ''} onChange={event => void saveField('skin_tendency', event.target.value || null)}><option value="">暂不填写</option>{profile.skin_tendency && !['偏干', '偏油', '混合', '中性', '不确定'].includes(profile.skin_tendency) && <option value={profile.skin_tendency}>之前填写：{profile.skin_tendency}</option>}<option value="偏干">偏干</option><option value="偏油">偏油</option><option value="混合">混合</option><option value="中性">中性</option><option value="不确定">不确定</option></select><small role="status">{fieldStatus.skin_tendency}</small></label>
          <label>喜欢的妆感<input value={listDraft.preferences || ''} onChange={event => setListDraft(current => ({ ...current, preferences: event.target.value }))} onBlur={() => saveList('preferences')} placeholder="自然、通勤、低饱和" /><small role="status">{fieldStatus.preferences || '输入后离开此处自动保存'}</small></label>
        </div>
        <details className="profile-extra-details"><summary>需要避开的成分与常用工具（选填）</summary><div className="profile-choice-grid">
          <label>敏感或需要避开<input value={listDraft.sensitivities || ''} onChange={event => setListDraft(current => ({ ...current, sensitivities: event.target.value }))} onBlur={() => saveList('sensitivities')} placeholder="例如：眼周敏感、避免香精" /><small role="status">{fieldStatus.sensitivities}</small></label>
          <label>常用工具<input value={listDraft.available_tools || ''} onChange={event => setListDraft(current => ({ ...current, available_tools: event.target.value }))} onBlur={() => saveList('available_tools')} placeholder="例如：美妆蛋、腮红刷" /><small role="status">{fieldStatus.available_tools}</small></label>
        </div></details>
      </section>
      <div className="profile-data-actions"><button type="button" className="profile-delete" disabled={deleteBusy} onClick={() => void deleteProfile()}>删除全部资料</button><span role="status">{deleteStatus}</span></div>
    </div>}
  </section>
}
