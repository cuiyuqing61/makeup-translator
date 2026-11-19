import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, request } from './api'
import { Clarification } from './AgentControls'
import { StepIllustrationDiagram } from './StepIllustration'
import ProfileView from './ProfileView'
import type {
  BackgroundTask,
  Health,
  PipelineResult,
  ProductRecord,
  ProductStatus,
  StepIllustration,
  TutorialStep,
} from './types'

type View = 'create' | 'products' | 'result' | 'profile'
const activeTaskStatuses = new Set(['queued', 'running', 'waiting_user'])
const stageLabel = (task?: BackgroundTask | null) => !task ? '等待分析' : task.status === 'succeeded' ? '已完成' :
  task.status === 'queued' || task.status === 'running' ? '处理中' : task.status === 'timed_out' ? '超时' :
  task.status === 'failed' ? '失败' : task.status === 'cancelled' ? '已停止' : '等待确认'

const regionLabels: Record<string, string> = {
  complexion: '底妆',
  eyebrow: '眉妆',
  eye: '眼妆',
  cheek: '颊彩',
  lip: '唇妆',
}

const categoryLabels: Record<string, string> = {
  base: '底妆',
  brow: '眉妆产品',
  eyeshadow: '眼影',
  eyeliner: '眼线',
  mascara: '睫毛膏',
  contour: '修容',
  highlighter: '高光',
  blush: '腮红',
  lip: '唇部产品',
  none: '无需产品',
  other: '其他',
}

const statusLabels: Record<ProductStatus, string> = {
  draft: '待补全',
  pending_user_review: '待你确认',
  confirmed: '已入库',
}

function splitEntries(value: string): string[] {
  return value
    .split(/[，,、\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function NavIcon({ name }: { name: View }) {
  const paths: Record<View, React.ReactNode> = {
    create: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z" /></>,
    products: <><path d="M4 8h16l-1 13H5z" /><path d="M9 9V6a3 3 0 0 1 6 0v3" /></>,
    result: <><rect x="5" y="3" width="14" height="18" rx="1" /><path d="M8 8h8M8 12h8M8 16h5" /></>,
    profile: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  }
  return <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function App() {
  const [onboarding, setOnboarding] = useState(() => !localStorage.getItem('onboarding-complete'))
  const [notice, setNotice] = useState('')
  const [history, setHistory] = useState<DiaryEntry[]>([])
  const [snapshotProducts, setSnapshotProducts] = useState<ProductRecord[]>([])
  const [view, setView] = useState<View>('create')
  useEffect(() => { window.scrollTo({top: 0, left: 0}) }, [view])
  const [products, setProducts] = useState<ProductRecord[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [result, setResult] = useState<PipelineResult | null>(null)
  const [selectedTutorialId, setSelectedTutorialId] = useState<string | null>(null)
  const [selectedTutorial, setSelectedTutorial] = useState<DiaryEntry | null>(null)
  const [referencePreview, setReferencePreview] = useState<string | null>(null)
  const [tutorialTask, setTutorialTask] = useState<BackgroundTask | null>(null)
  useEffect(() => {
    if (view !== 'result') return
    let active = true
    const load = () => request<DiaryEntry[]>('/api/tutorials').then(items => { if (active) setHistory(items) }).catch(e => { if (active) setNotice(e.message) })
    void load()
    const timer = window.setInterval(load, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [view])
  useEffect(() => {
    if (!selectedTutorialId) return
    let active = true
    const load = () => request<DiaryEntry>(`/api/tutorials/${selectedTutorialId}`).then(item => {
      if (!active) return
      setSelectedTutorial(item)
      setResult(item.steps_result || (item.task.result?.tutorial ? item.task.result as unknown as PipelineResult : null))
    }).catch(error => { if (active) setNotice(String(error)) })
    void load()
    const timer = window.setInterval(load, 2500)
    return () => { active = false; window.clearInterval(timer) }
  }, [selectedTutorialId])
  const [productTasks, setProductTasks] = useState<Record<string, BackgroundTask>>({})
  const [colorTasks, setColorTasks] = useState<Record<string, BackgroundTask>>({})

  const loadProducts = useCallback(async () => {
    setProducts(await api.listProducts())
  }, [])

  useEffect(() => {
    void Promise.all([
      loadProducts(),
      api.health().then(setHealth),
      api.listTasks().then((tasks) => {
        const latestTutorial = tasks.find((task) => ['tutorial', 'tutorial_analysis'].includes(task.kind) && activeTaskStatuses.has(task.status))
        if (latestTutorial) { setTutorialTask(latestTutorial); setSelectedTutorialId(latestTutorial.id) }
        const restoredProducts: Record<string, BackgroundTask> = {}
        const restoredColors: Record<string, BackgroundTask> = {}
        for (const task of tasks) {
          if (task.kind === 'product_enrichment' && task.target_id && !restoredProducts[task.target_id]) {
            restoredProducts[task.target_id] = task
          }
          if (task.kind === 'product_color' && task.target_id && !restoredColors[task.target_id]) restoredColors[task.target_id] = task
        }
        setProductTasks(restoredProducts)
        setColorTasks(restoredColors)
      }),
    ]).catch(e => { setHealth(null); setNotice('加载失败，请刷新重试：' + e.message) })
  }, [loadProducts])

  const activeTaskKey = [
    tutorialTask && activeTaskStatuses.has(tutorialTask.status) ? tutorialTask.id : '',
    ...Object.values(productTasks)
      .filter((task) => activeTaskStatuses.has(task.status))
      .map((task) => task.id)
      .sort(),
    ...Object.values(colorTasks)
      .filter((task) => activeTaskStatuses.has(task.status))
      .map((task) => task.id)
      .sort(),
  ].join('|')

  useEffect(() => {
    const taskIds = activeTaskKey.split('|').filter(Boolean)
    if (taskIds.length === 0) return
    let stopped = false
    const poll = async () => {
      const updates = await Promise.all(taskIds.map((id) => api.getTask(id).catch(() => null)))
      if (stopped) return
      let productsChanged = false
      for (const task of updates) {
        if (!task) continue
        if (task.kind === 'tutorial' || task.kind === 'tutorial_analysis') {
          setTutorialTask(task)
          if (task.status === 'succeeded' && task.result) {
            setNotice(task.kind === 'tutorial_analysis' ? '参考妆面分析已完成，步骤与产品正在分别处理。' : '教程已完成，可以在妆容日记查看。')
          }
        } else if (task.kind === 'product_color' && task.target_id) {
          setColorTasks((current) => ({ ...current, [task.target_id!]: task }))
          if (task.status === 'succeeded') productsChanged = true
        } else if (task.target_id) {
          setProductTasks((current) => ({ ...current, [task.target_id!]: task }))
          if (task.status === 'succeeded') productsChanged = true
        }
      }
      if (productsChanged) await loadProducts().catch(e => setNotice(e.message))
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 1000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [activeTaskKey, loadProducts])

  useEffect(
    () => () => {
      if (referencePreview) URL.revokeObjectURL(referencePreview)
    },
    [referencePreview],
  )

  const startTutorialTask = (task: BackgroundTask, file: File) => {
    setResult(null)
    setSelectedTutorial(null)
    setSelectedTutorialId(task.id)
    if (referencePreview) URL.revokeObjectURL(referencePreview)
    setReferencePreview(URL.createObjectURL(file))
    setSnapshotProducts(products.filter((product) => product.status === 'confirmed'))
    setTutorialTask(task)
  }

  const cancelTutorialTask = async () => {
    if (!tutorialTask) return
    try { setTutorialTask(await api.cancelTask(tutorialTask.id)) } catch(e) { setNotice(String(e)) }
  }

  const startProductTask = (task: BackgroundTask) => {
    if (task.target_id) {
      if (task.kind === 'product_color') setColorTasks((current) => ({ ...current, [task.target_id!]: task }))
      else setProductTasks((current) => ({ ...current, [task.target_id!]: task }))
    }
  }

  const cancelProductTask = async (productId: string) => {
    const task = productTasks[productId]
    if (!task) return
    try { const cancelled = await api.cancelTask(task.id); setProductTasks((current) => ({ ...current, [productId]: cancelled })) } catch(e) { setNotice(String(e)) }
  }

  const cancelColorTask = async (productId: string) => {
    const task = colorTasks[productId]
    if (!task) return
    try { const cancelled = await api.cancelTask(task.id); setColorTasks((current) => ({ ...current, [productId]: cancelled })) } catch(e) { setNotice(String(e)) }
  }

  const confirmedCount = products.filter((item) => item.status === 'confirmed').length

  return (
    <div className="app-shell">
      <header className="site-header">
        <button className="brand" onClick={() => setView('create')} aria-label="返回生成教程">
          <span className="brand-mark">妆译</span>
          <span className="brand-subtitle">MAKEUP TRANSLATOR</span>
        </button>
        <nav aria-label="主导航">
          <button className={view === 'create' ? 'active' : ''} onClick={() => setView('create')}>
            <NavIcon name="create" /><span>梳妆台</span>
          </button>
          <button className={view === 'products' ? 'active' : ''} onClick={() => setView('products')}>
            <NavIcon name="products" /><span>我的化妆包</span>
            {confirmedCount > 0 && <span className="nav-count">{confirmedCount}</span>}
          </button>
          <button className={view === 'result' ? 'active' : ''} onClick={() => setView('result')}>
            <NavIcon name="result" /><span>妆容日记</span>
          </button>
          <button className={view === 'profile' ? 'active' : ''} onClick={() => setView('profile')}><NavIcon name="profile" /><span>我的资料</span></button>
        </nav>
      </header>

      <main className={view === 'create' ? 'main-create' : 'main-inner'}>
        {notice && <div className="notice app-toast" role="status">{notice} <button onClick={() => setView('result')}>查看日记</button><button onClick={() => setNotice('')}>关闭</button></div>}
        <div hidden={view !== 'profile'}><ProfileView /></div>
        {view === 'result' && <section className="diary">
          <span className="eyebrow">YOUR LOOKS, IN ONE PLACE</span><h1>妆容日记</h1><p>每一次灵感，都留在这里。</p>
          {history.length === 0 && <div className="empty-state diary-empty"><span className="diary-empty-mark" aria-hidden="true">✧</span><h2>从第一张妆面开始</h2><p>上传喜欢的妆容，完成后就能在这里找回教程。</p><button onClick={() => setView('create')}>开始第一次生成 <span aria-hidden="true">↗</span></button></div>}
          {history.map(item => <article className="diary-card" key={item.id}>
            <img src={item.image_url} alt="参考妆面" />
            <div>
              <strong>{item.provider === 'fixture' ? '演示 · ' : ''}{item.steps_result?.tutorial.title || (item.task.result?.tutorial ? (item.task.result as unknown as PipelineResult).tutorial.title : '复刻妆容')}</strong>
              <p>复刻步骤：{stageLabel(item.stages?.steps || item.task)} · 产品清单：{stageLabel(item.stages?.products)}</p>
              {item.stages?.analysis && <small>参考图分析：{stageLabel(item.stages.analysis)}</small>}
              {Boolean(item.steps_result || item.products_result || item.task.result?.tutorial) && <button onClick={() => { setSelectedTutorialId(item.id); setSelectedTutorial(item); setResult(item.steps_result || (item.task.result?.tutorial ? item.task.result as unknown as PipelineResult : null)); setReferencePreview(item.image_url); setSnapshotProducts(item.product_records); setTutorialTask(item.task) }}>查看结果</button>}
              {(['analysis', 'steps', 'products'] as const).map(stage => {
                const task = item.stages?.[stage]
                return task && ['failed','timed_out','cancelled'].includes(task.status) ? <button key={stage} onClick={async () => { try { await request(`/api/tutorials/${item.id}/retry?stage=${stage}`, {method:'POST'}); setHistory(await request<DiaryEntry[]>('/api/tutorials')) } catch(e) { setNotice(String(e)) } }}>重试{stage === 'analysis' ? '分析' : stage === 'steps' ? '步骤' : '产品'}</button> : null
              })}
              {item.stages?.analysis ? (['analysis','steps','products'] as const).map(stage => {
                const task = item.stages?.[stage]
                return task?.cancellable ? <button key={stage} onClick={async () => { try { await api.cancelTask(task.id); setHistory(await request<DiaryEntry[]>('/api/tutorials')) } catch(e) { setNotice(String(e)) } }}>停止{stage === 'analysis' ? '分析' : stage === 'steps' ? '步骤' : '产品'}</button> : null
              }) : item.task.cancellable && <button onClick={async () => { try { setTutorialTask(await api.cancelTask(item.id)) } catch(e) { setNotice(String(e)) } }}>停止任务</button>}
              <button onClick={async () => { try { await request('/api/tutorials/' + item.id, {method:'DELETE'}); setHistory(history.filter(x => x.id !== item.id)); if (selectedTutorialId === item.id) { setResult(null); setSelectedTutorial(null); setSelectedTutorialId(null) } } catch(e) { setNotice(String(e)) } }}>删除记录</button>
            </div>
          </article>)}
        </section>}

        <div hidden={view !== 'create'}>{(
          <CreateTutorial
            qwenConfigured={health?.qwen_configured ?? null}
            confirmedCount={confirmedCount}
            activeTask={tutorialTask}
            onTaskStarted={startTutorialTask}
            onCancel={() => void cancelTutorialTask()}
            onOpenProducts={() => setView('products')}
            onOpenDiary={() => setView('result')}
          />
        )}
        {onboarding && <aside className="onboarding-prompt"><div><strong>先留下你的偏好，之后只需上传妆面。</strong><p>熟练度、肤质和常用工具可以在“我的资料”里随时修改。</p></div><div className="onboarding-actions"><button onClick={() => { setView('profile'); setOnboarding(false); localStorage.setItem('onboarding-complete','1') }}>填写我的资料</button><button onClick={() => { setOnboarding(false); localStorage.setItem('onboarding-complete','1') }}>先跳过</button></div></aside>}
        </div><div hidden={view !== 'products'}>{(
          <ProductsView
            products={products}
            tasks={productTasks}
            onTaskStarted={startProductTask}
            onCancelTask={(id) => void cancelProductTask(id)}
            colorTasks={colorTasks}
            onCancelColorTask={(id) => void cancelColorTask(id)}
            onProductsChanged={loadProducts}
          />
        )}
        </div>{view === 'result' && (result || selectedTutorial?.products_result) && (
          <ResultView
            tutorialId={selectedTutorialId ?? tutorialTask?.id ?? null}
            result={result}
            entry={selectedTutorial}
            referencePreview={referencePreview}
            products={snapshotProducts.length ? snapshotProducts : products}
            onCreateAnother={() => setView('create')}
          />
        )}
      </main>

      <footer>
        <span>妆译 · 把灵感翻译成适合你的方法</span>
        <span>结果为可行复刻建议，请结合自身情况调整</span>
      </footer>
    </div>
  )
}

function CreateTutorial({
  qwenConfigured,
  confirmedCount,
  activeTask,
  onTaskStarted,
  onCancel,
  onOpenProducts,
  onOpenDiary,
}: {
  qwenConfigured: boolean | null
  confirmedCount: number
  activeTask: BackgroundTask | null
  onTaskStarted: (task: BackgroundTask, file: File) => void
  onCancel: () => void
  onOpenProducts: () => void
  onOpenDiary: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [showFullImage, setShowFullImage] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const formRef = useRef<HTMLFormElement>(null)
  const busy = submitting || Boolean(activeTask && activeTaskStatuses.has(activeTask.status))

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview)
    },
    [preview],
  )

  const chooseFile = (nextFile: File | null) => {
    if (preview) URL.revokeObjectURL(preview)
    setFile(nextFile)
    setPreview(nextFile ? URL.createObjectURL(nextFile) : null)
    setError('')
    if (nextFile) {
      window.requestAnimationFrame(() => formRef.current?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      }))
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!file) {
      setError('请先上传一张参考妆面图片。')
      return
    }
    setSubmitting(true)
    setError('')
    const form = new FormData()
    form.append('image', file)
    form.append('provider', 'qwen')
    try {
      onTaskStarted(await api.createTutorial(form), file)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '教程生成失败，请稍后重试。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="create-page page-enter">
      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-content">
          <h1 id="landing-title">喜欢的妆，<br />现在就上手。</h1>
          <p>上传参考妆面，生成适合你的上妆步骤。</p>
          <div className="landing-actions">
            <button className="hero-action" type="button" onClick={() => inputRef.current?.click()}>上传妆面 <span aria-hidden="true">↗</span></button>
            <button className="quiet-link" type="button" onClick={onOpenDiary}>查看妆容日记</button>
          </div>
          <small>支持 JPG、PNG、WEBP 图片</small>
        </div>
      </section>

      <section className="journey-strip" aria-label="生成教程的三个步骤">
        <div><span>01</span><strong>读懂妆面</strong><small>识别参考图中的妆容特点</small></div>
        <div><span>02</span><strong>匹配手边产品</strong><small>结合你的资料与化妆包</small></div>
        <div><span>03</span><strong>生成步骤</strong><small>得到可操作的复刻教程</small></div>
      </section>

      <section className="create-workspace" aria-labelledby="workspace-title">
        <div className="workspace-intro">
          <span className="eyebrow">YOUR LOOK, YOUR WAY</span>
          <h2 id="workspace-title">从一张妆面开始</h2>
          <p>选择一张喜欢的妆面，生成完整复刻教程。</p>
          <p className="workspace-note">面部资料和长期偏好会自动带入。</p>
        </div>
      <form ref={formRef} className="generator-card" onSubmit={submit}>
        <div className="card-heading">
          <div><h2>选择参考妆面</h2></div>
          <span className="privacy-note">图片保存在本地日记，可删除</span>
        </div>

        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          hidden
          onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
        />
        <button
          className={`upload-zone ${preview ? 'has-image' : ''}`}
          type="button"
          onClick={() => inputRef.current?.click()}
        >
          {preview ? (
            <><img src={preview} alt="待分析的参考妆面" /><span className="replace-image">更换图片</span></>
          ) : (
            <span className="upload-empty">
              <span className="upload-icon">＋</span>
              <strong>上传你喜欢的妆面</strong>
              <small>JPG、PNG 或 WEBP，最大 12 MB</small>
            </span>
          )}
        </button>
        {preview && <button type="button" className="preview-full-button" onClick={() => setShowFullImage(true)}>查看完整图片</button>}
        {showFullImage && preview && createPortal(<div className="preview-backdrop" role="presentation" onClick={() => setShowFullImage(false)}>
          <div className="preview-dialog" role="dialog" aria-modal="true" aria-label="完整参考妆面" onClick={(event) => event.stopPropagation()}>
            <button type="button" aria-label="关闭图片" onClick={() => setShowFullImage(false)}>关闭 ×</button>
            <img src={preview} alt="完整参考妆面" />
          </div>
        </div>, document.body)}

        <div className="product-connection">
          <div><span className="bag-icon">◇</span><p><strong>{confirmedCount > 0 ? `将优先使用化妆包中的 ${confirmedCount} 件产品` : '还没有录入已有产品'}</strong><small>产品不是生成教程的必要条件，也可以稍后补充</small></p></div>
          <button type="button" onClick={onOpenProducts}>{confirmedCount ? '查看化妆包' : '去添加'}</button>
        </div>

        {qwenConfigured === false && <p className="inline-warning">真实分析服务尚未配置，请先配置视觉模型 API。</p>}
        {activeTask && ['failed', 'timed_out', 'cancelled'].includes(activeTask.status) && (
          <p className="form-error" role="alert">{activeTask.error ?? activeTask.message}</p>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="primary-action" disabled={busy}>
          {busy ? <><span className="spinner" />{activeTask?.message ?? '正在创建任务'}</> : <>生成我的复刻教程 <span>→</span></>}
        </button>
        {activeTask && activeTaskStatuses.has(activeTask.status) && (
          <TaskProgress task={activeTask} onCancel={onCancel} />
        )}
      </form>
      </section>
    </div>
  )
}

function ProductsView({
  products,
  tasks,
  onTaskStarted,
  onCancelTask,
  colorTasks,
  onCancelColorTask,
  onProductsChanged,
}: {
  products: ProductRecord[]
  tasks: Record<string, BackgroundTask>
  onTaskStarted: (task: BackgroundTask) => void
  onCancelTask: (productId: string) => void
  colorTasks: Record<string, BackgroundTask>
  onCancelColorTask: (productId: string) => void
  onProductsChanged: () => Promise<void>
}) {
  const [packagePhoto, setPackagePhoto] = useState<File | null>(null)
  const [openedPhoto, setOpenedPhoto] = useState<File | null>(null)
  const [photoMessage, setPhotoMessage] = useState('')
  const [recognizing, setRecognizing] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [brand, setBrand] = useState('')
  const [name, setName] = useState('')
  const [shade, setShade] = useState('')
  const [market, setMarket] = useState('中国大陆')
  useEffect(() => { const select = (event: Event) => { const {id,candidate} = (event as CustomEvent).detail; setTimeout(() => { setEditingId(id); setBrand(candidate.brand); setName(candidate.product_name); setShade(candidate.model_or_shade || ''); setMarket(candidate.market || '') }, 0) }; window.addEventListener('select-candidate',select); return () => window.removeEventListener('select-candidate',select) }, [])
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [message, setMessage] = useState('')
  const [actionMessages, setActionMessages] = useState<Record<string, string>>({})

  const addProduct = async (event: FormEvent) => {
    event.preventDefault()
    setAdding(true)
    setMessage('')
    try {
      const input = { brand, name, model_or_shade: shade || undefined, market: market || undefined };
      const previous = products.find(p => p.id === editingId)
      const unchanged = previous && previous.raw_brand === brand && previous.raw_name === name &&
        (previous.raw_model_or_shade || '') === shade && (previous.raw_market || '') === market
      const saved = editingId ? (unchanged ? previous : await request<ProductRecord>('/api/products/' + editingId, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(input) })) : await api.createProduct(input);
      for (const [role, file] of [['package', packagePhoto], ['opened', openedPhoto]] as const) {
        if (file) {
          const form = new FormData(); form.append('image', file)
          const upload = await request<{product: ProductRecord; task: BackgroundTask | null}>(`/api/products/${saved.id}/photos/${role}`, {method:'POST', body:form})
          if (upload.task) onTaskStarted(upload.task)
        }
      }
      setPackagePhoto(null); setOpenedPhoto(null); setPhotoMessage('')
      setEditingId(null)
      setBrand(''); setName(''); setShade('')
      await onProductsChanged()
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '添加失败。')
      setPhotoMessage(reason instanceof Error ? reason.message : '照片上传失败。')
    } finally {
      setAdding(false)
    }
  }

  const act = async (id: string, action: 'enrich' | 'refresh' | 'confirm' | 'confirm-refresh' | 'confirm-unverified' | 'delete' | 'reset' | 'edit') => {
    setMessage('')
    setActionMessages(current => ({...current, [id]: ''}))
    try {
      if (action === 'enrich' || action === 'refresh') {
        onTaskStarted(await api.enrichProduct(id, action === 'refresh'))
      } else if (action === 'delete' || action === 'reset') {
        await request(`/api/products/${id}${action === 'reset' ? '/reset' : ''}`, {method: action === 'delete' ? 'DELETE' : 'POST'}); await onProductsChanged()
      } else if (action === 'edit') {
        setEditingId(id); const p = products.find(p => p.id === id)!; setBrand(p.raw_brand); setName(p.raw_name); setShade(p.raw_model_or_shade || ''); setMarket(p.raw_market || ''); setPackagePhoto(null); setOpenedPhoto(null)
      } else {
        setConfirmingId(id)
        await api.confirmProduct(id, action === 'confirm-unverified')
        await onProductsChanged()
      }
    } catch (reason) {
      setActionMessages(current => ({...current, [id]: reason instanceof Error ? reason.message : '操作失败。'}))
    } finally {
      setConfirmingId(null)
    }
  }

  const ordered = useMemo(() => [...products].sort((a, b) => b.updated_at.localeCompare(a.updated_at)), [products])

  return (
    <div className="products-page page-enter">
      <section className="page-intro">
        <span className="eyebrow">YOUR BEAUTY SHELF</span>
        <h1>我的化妆包</h1>
        <p>填写包装上的产品信息即可录入。包装照帮助核对名称，打开后的产品照帮助比较妆效颜色；两张照片均可选。</p>
      </section>

      <div className="products-layout">
        <form className="add-product-card" onSubmit={addProduct}>
          <span className="step-kicker">ADD A PRODUCT</span>
          <h2>{editingId ? '编辑产品与照片' : '录入一件产品'}</h2>
          <div className="product-photo-grid">
            <div className="product-photo-field"><span>外包装照片 · 可选</span><label className="product-photo-picker"><input className="sr-only" type="file" accept="image/jpeg,image/png,image/webp" onChange={e => {setPackagePhoto(e.target.files?.[0] || null); e.target.value=''; setPhotoMessage('')}} /><span className="product-photo-icon" aria-hidden="true">＋</span><strong>{packagePhoto ? '已选择包装照' : '选择外包装'}</strong><small>{packagePhoto?.name || '正面或色号标签，帮助核对产品信息'}</small></label></div>
            <div className="product-photo-field"><span>打开后的产品照 · 可选</span><label className="product-photo-picker"><input className="sr-only" type="file" accept="image/jpeg,image/png,image/webp" onChange={e => {setOpenedPhoto(e.target.files?.[0] || null); e.target.value=''; setPhotoMessage('')}} /><span className="product-photo-icon" aria-hidden="true">＋</span><strong>{openedPhoto ? '已选择产品照' : '选择产品本体'}</strong><small>{openedPhoto?.name || '均匀白光下拍摄膏体或色盘，避开反光'}</small></label></div>
          </div>
          {photoMessage && <p className="photo-inline-message" role="alert">{photoMessage}</p>}
          <button className="recognize-action" type="button" disabled={!packagePhoto || recognizing} onClick={async () => { setRecognizing(true); setPhotoMessage('正在读取包装文字…'); try { const form = new FormData(); form.append('images', packagePhoto!); const r = await request<Record<string,string>>('/api/photos/recognize',{method:'POST',body:form}); if (!brand) setBrand(r.brand || ''); if (!name) setName(r.name || ''); if (!shade) setShade(r.model_or_shade || ''); if (!market && r.market) setMarket(r.market); setPhotoMessage('识别完成，请核对信息。' + (r.notes || '')) } catch(e) { setPhotoMessage(String(e)) } finally {setRecognizing(false)} }}> {recognizing ? '识别中…' : '从包装照读取名称'} </button>
          <label><span>品牌 *</span><input required value={brand} onChange={(event) => setBrand(event.target.value)} placeholder="例如：Maybelline" /></label>
          <label><span>产品名称 *</span><input required value={name} onChange={(event) => setName(event.target.value)} placeholder="包装上的完整名称" /></label>
          <label><span>型号或色号</span><input value={shade} onChange={(event) => setShade(event.target.value)} placeholder="例如：120 Classic Ivory" /></label>
          <label><span>购买地区</span><input value={market} onChange={(event) => setMarket(event.target.value)} /></label>
          <button className="primary-action compact" disabled={adding}>{adding ? '正在添加…' : editingId ? '保存修改' : '添加为待补全产品'}</button>
          <p className="form-footnote">添加后再启动联网补全，避免输错时产生不必要的搜索。</p>
        </form>

        <section className="product-list">
          <div className="list-heading"><h2>{products.length} 件产品</h2><span>{products.filter((item) => item.status === 'confirmed').length} 件已确认</span></div>
          {message && <p className="form-error" role="alert">{message}</p>}
          {ordered.length === 0 && <div className="empty-state"><span>◇</span><h3>化妆包还是空的</h3><p>先录入一件你最常用的产品吧。</p></div>}
          <div className="batch-actions"><button onClick={async () => { try { const r = await request<{tasks: BackgroundTask[], errors: Record<string,string>}>('/api/products/batch/enrich', {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(selected)}); r.tasks.forEach(onTaskStarted); setMessage(Object.values(r.errors).join('；')) } catch(e) { setMessage(String(e)) } }}>补全所选（{selected.length}）</button><button onClick={() => selected.forEach(onCancelTask)}>停止所选</button><small>同时处理 3 件，其余排队</small></div>
          {ordered.map((product) => (<div key={product.id}><label className="select-product"><input type="checkbox" checked={selected.includes(product.id)} onChange={e => setSelected(e.target.checked ? [...selected, product.id] : selected.filter(id => id !== product.id))} />选择 {product.raw_name}</label>
            <ProductCard
              key={product.id}
              product={product}
              task={tasks[product.id]}
              colorTask={colorTasks[product.id]}
              confirming={confirmingId === product.id}
              actionMessage={actionMessages[product.id]}
              onCancel={() => onCancelTask(product.id)}
              onCancelColor={() => onCancelColorTask(product.id)}
              onAction={(action) => void act(product.id, action)}
              onColorRetry={async () => { try { const result = await request<{task: BackgroundTask | null}>(`/api/products/${product.id}/colors/analyze`, {method:'POST'}); if(result.task) onTaskStarted(result.task); await onProductsChanged() } catch(e) { setMessage(String(e)) } }}
              onRegionsSaved={onProductsChanged}
              onPhotoDelete={async (role) => { try { await request(`/api/products/${product.id}/photos/${role}`, {method:'DELETE'}); await onProductsChanged() } catch(e) { setMessage(String(e)) } }}
            /></div>
          ))}
        </section>
      </div>
    </div>
  )
}

function ProductCard({
  product,
  task,
  colorTask,
  confirming,
  actionMessage,
  onCancel,
  onCancelColor,
  onAction,
  onColorRetry,
  onRegionsSaved,
  onPhotoDelete,
}: {
  product: ProductRecord
  task?: BackgroundTask
  colorTask?: BackgroundTask
  confirming: boolean
  actionMessage?: string
  onCancel: () => void
  onCancelColor: () => void
  onAction: (action: 'enrich' | 'refresh' | 'confirm' | 'confirm-refresh' | 'confirm-unverified' | 'delete' | 'reset' | 'edit') => void
  onColorRetry: () => void
  onRegionsSaved: () => Promise<void>
  onPhotoDelete: (role: 'package' | 'opened') => void
}) {
  const enrichment = product.enrichment
  const [marking, setMarking] = useState(false)
  const [marks, setMarks] = useState<{x: number; y: number; box: number[]}[]>([])
  const [markError, setMarkError] = useState('')
  const [savingMarks, setSavingMarks] = useState(false)
  const taskRunning = Boolean(task && activeTaskStatuses.has(task.status))
  const busy = confirming || taskRunning
  const partial = task?.result?.status === 'partial' && task.result.product_updated_at === product.updated_at ? task.result as Record<string, unknown> : null
  const refreshCandidate = task?.result?.product_updated_at === product.updated_at ?
    task?.result?.refresh_candidate as Record<string, unknown> | undefined : undefined
  return (
    <article className={`product-card status-${product.status}`}>
      <div className="product-summary">
        <div className="product-swatch">{product.photo_urls?.[0] ? <img style={{width:60,height:70,objectFit:'cover',borderRadius:12}} src={product.photo_urls[0]} alt="产品包装" /> : <span>{product.raw_brand.slice(0, 1).toUpperCase()}</span>}</div>
        <div className="product-name"><span className={`status-pill ${product.status}`}>{statusLabels[product.status]}</span><h3>{enrichment?.canonical_name ?? product.raw_name}</h3><p>{enrichment?.canonical_brand ?? product.raw_brand}{(enrichment?.model_or_shade ?? product.raw_model_or_shade) ? ` · ${enrichment?.model_or_shade ?? product.raw_model_or_shade}` : ''}</p></div>
        <div className="product-actions"><button onClick={() => onAction('edit')}>编辑</button><button onClick={() => onAction('delete')}>删除</button>{product.status !== 'draft' && <button onClick={() => onAction('reset')}>{product.status === 'confirmed' ? '撤回确认' : '驳回回草稿'}</button>}
          {product.status === 'draft' && <button className="small-action" disabled={busy} onClick={() => onAction('enrich')}>{busy ? <><span className="spinner dark" />核验中</> : task && ['failed','timed_out','cancelled'].includes(task.status) ? '继续核验' : '补全并核验'}</button>}
          {product.status === 'draft' && task && <details className="product-more-actions"><summary>更多操作</summary><button disabled={busy} onClick={() => onAction('refresh')}>更新来源</button></details>}
          {product.status === 'pending_user_review' && <button className="small-action" disabled={busy} onClick={() => onAction('confirm')}>{busy ? '处理中…' : '确认入库'}</button>}
          {product.status === 'pending_user_review' && <details className="product-more-actions"><summary>更多操作</summary><button disabled={busy} onClick={() => onAction('refresh')}>更新来源</button></details>}
          {product.status === 'confirmed' && <><span className="confirmed-mark">✓ 已用于教程匹配</span><button disabled={busy} onClick={() => onAction('refresh')}>更新来源</button></>}
          {refreshCandidate && <button disabled={busy} onClick={() => onAction('confirm-refresh')}>确认新来源</button>}
        </div>
      </div>
      {actionMessage && <p className="product-task-error" role="alert">{actionMessage}</p>}
      {(product.photo_urls?.[0] || product.opened_photo_url) && <div className="product-photo-summary">
        {product.photo_urls?.[0] && <div><img src={product.photo_urls[0]} alt="外包装" /><span>外包装</span><button type="button" onClick={() => onPhotoDelete('package')}>移除</button></div>}
        {product.opened_photo_url && <div><img src={product.opened_photo_url} alt="打开后的产品" /><span>产品本体</span><button type="button" onClick={() => onPhotoDelete('opened')}>移除</button></div>}
        <button type="button" onClick={() => onAction('edit')}>更换照片</button>
      </div>}
      {product.opened_photo_url && <div className="product-colors">
        <strong>照片里的可见颜色</strong>
        {product.color_samples?.length ? <div className="product-color-list">{product.color_samples.map((sample, index) => <span key={`${sample.label}-${index}`}><i style={{backgroundColor:sample.hex}} />{sample.label}<small>{sample.confidence === 'low' ? '需复核' : '照片取色'}</small></span>)}</div> : <small>尚无可靠色块。建议在均匀白光下拍摄打开后的产品，避开反光。</small>}
        {colorTask && activeTaskStatuses.has(colorTask.status) && <TaskProgress task={colorTask} onCancel={onCancelColor} compact />}
        {colorTask && ['failed','timed_out'].includes(colorTask.status) && <small role="alert">{colorTask.error || colorTask.message}</small>}
        {!product.color_samples?.length && !(colorTask && activeTaskStatuses.has(colorTask.status)) && <button type="button" onClick={onColorRetry}>重新识别颜色</button>}
        <button type="button" onClick={() => {setMarking(!marking); setMarks([]); setMarkError('')}}>{marking ? '取消标记' : '手动标记色块'}</button>
        {marking && <div className="color-mark-editor">
          <p>点击产品内部的颜色区域；多色盘可逐一点击，最多 16 处。标记会重新取代当前颜色记录。</p>
          <div className="color-mark-canvas"><img src={product.opened_photo_url} alt="点击产品色块中心进行标记" onClick={event => {
            if (marks.length >= 16) return
            const rect = event.currentTarget.getBoundingClientRect()
            const x = (event.clientX-rect.left)/rect.width
            const y = (event.clientY-rect.top)/rect.height
            const radius = .07
            setMarks([...marks, {x,y,box:[Math.max(0,x-radius), Math.max(0,y-radius), Math.min(1,x+radius), Math.min(1,y+radius)]}])
          }} />{marks.map((mark,index) => <span className="color-mark-dot" key={index} style={{left:`${mark.x*100}%`, top:`${mark.y*100}%`}}>{index+1}</span>)}</div>
          <div className="color-mark-actions"><button type="button" onClick={() => setMarks(marks.slice(0,-1))} disabled={!marks.length}>撤销上一个</button><button type="button" disabled={!marks.length || savingMarks} onClick={async () => {setSavingMarks(true); setMarkError(''); try { await request(`/api/products/${product.id}/colors/regions`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({regions:marks.map((mark,index) => ({label:`色块 ${index+1}`,box:mark.box}))})}); await onRegionsSaved(); setMarking(false); setMarks([]) } catch(e) {setMarkError(String(e))} finally {setSavingMarks(false)} }}>{savingMarks ? '保存中…' : `保存 ${marks.length} 个色块`}</button></div>
          {markError && <small role="alert">{markError}</small>}
        </div>}
      </div>}
      {taskRunning && task && <TaskProgress task={task} onCancel={onCancel} compact />}
      {partial && <div className="product-partial-card"><strong>部分核验结果 · 待继续</strong><p>{String(partial.reason || '')}</p><small>原始输入：{String((partial.input as Record<string, unknown>)?.brand || '')} {String((partial.input as Record<string, unknown>)?.name || '')}{(partial.input as Record<string, unknown>)?.model_or_shade ? ` · ${String((partial.input as Record<string, unknown>).model_or_shade)}` : ''}</small>{Object.entries(partial.verified_fields as Record<string, {value: unknown}> || {}).map(([field, evidence]) => <small key={field}>已核实 {field}：{String(evidence.value)}</small>)}{Boolean((partial.sources as unknown[] || []).length) && <><small>待核实：{(partial.pending_fields as string[] || []).join('、') || '暂无'}</small>{Boolean((partial.missing_fields as string[] || []).length) && <small>缺失信息：{(partial.missing_fields as string[]).join('、')}</small>}</>}<details><summary>检索进度与来源</summary><p>{String(partial.search_progress || '')}</p>{(partial.sources as {url: string; title: string}[] || []).map((source, i) => <p key={i}><a href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a></p>)}</details></div>}
      {task && <Clarification task={task} />}
      {task && ['failed', 'timed_out', 'cancelled'].includes(task.status) && product.status === 'draft' && (
        <p className="product-task-error">{task.error ?? task.message}</p>
      )}
      {enrichment && (
        <details className="evidence-panel" open={product.status === 'pending_user_review'}>
          <summary>查看补全资料与依据 <span>{enrichment.sources.length} 个来源</span></summary>
          <div className="evidence-content">
            <div className="fact-grid">
              <div><span>匹配判断</span><strong>{enrichment.match_status === 'exact_match' ? '精确匹配' : enrichment.match_status === 'likely_match' ? '可能匹配' : '存在歧义'}</strong></div>
              <div><span>产品类别</span><strong>{categoryLabels[enrichment.category] ?? enrichment.category}</strong></div>
              <div><span>色彩</span><strong>{enrichment.color_family.join('、') || '待确认'}</strong></div>
              <div><span>妆效</span><strong>{enrichment.finish.join('、') || '待确认'}</strong></div>
            </div>
            {enrichment.official_description && <p className="official-copy">{enrichment.official_description}</p>}
            {Object.entries(enrichment.field_evidence || {}).map(([field, evidence]) => <blockquote key={field}><strong>{field}</strong><p>{evidence.excerpt}</p><a href={evidence.url} target="_blank" rel="noreferrer">查看原文</a><small> · {evidence.checked_at}</small></blockquote>)}
            {enrichment.unresolved_fields.length > 0 && <p className="unresolved">待确认字段：{enrichment.unresolved_fields.join('、')}</p>}
            {enrichment.alternatives?.map((candidate,i) => <div className="candidate" key={i}><strong>{candidate.brand} {candidate.product_name} {candidate.model_or_shade}</strong><p>{candidate.difference}</p><a href={candidate.url} target="_blank" rel="noreferrer">查看候选依据</a><button onClick={() => { window.dispatchEvent(new CustomEvent('select-candidate',{detail:{id:product.id,candidate}})); onAction('edit') }}>选择并编辑此候选</button></div>)}
            <p>{enrichment.image_comparison || '未进行包装图片比对，请对照包装确认型号与地区版本。'}</p>
            <p>测评观察（公开搜索摘要）：{enrichment.review_observations.join('；') || '暂无可验证测评'}</p>
            {[...enrichment.conflicts, ...enrichment.validation_warnings].map((x,i) => <p className="inline-warning" key={i}>{x}</p>)}
            <div className="source-list">
              {enrichment.sources.map((source) => (
                <a key={source.url} href={source.url} target="_blank" rel="noreferrer">
                  <span className={`source-dot ${source.validation?.reachable ? 'reachable' : ''}`} />
                  <span><strong>{source.title}</strong><small>{source.source_type} · {source.validation ? (source.validation.reachable ? '页面可访问' : '无法访问') : '未校验'}</small></span>
                  <b>↗</b>
                </a>
              ))}
            </div>
            {enrichment.search_audit && <p className="audit-line">本次补全耗时 {(enrichment.search_audit.elapsed_ms / 1000).toFixed(1)} 秒 · {enrichment.search_audit.status}</p>}
            {product.status === 'pending_user_review' && <button className="text-action" disabled={busy} onClick={() => onAction('confirm-unverified')}>我已对照包装确认，仍要入库</button>}
          </div>
        </details>
      )}
    </article>
  )
}

function TaskProgress({ task, onCancel, compact = false }: { task: BackgroundTask; onCancel: () => void; compact?: boolean }) {
  return (
    <div className={`task-progress ${compact ? 'compact' : ''}`}>
      <div className="task-progress-heading">
        <span>{task.message}</span>
        <strong>{task.status === 'queued' ? '排队中' : `阶段估计 ${task.progress}%`}</strong>
      </div>
      <div className="progress-track"><span style={{ width: `${task.progress}%` }} /></div>
      <div className="task-progress-foot">
        <span>
          {task.status === 'queued' ? `排队 ${Math.round(task.queued_seconds || 0)} 秒 · 前方 ${Math.max(0,(task.queue_position || 1)-1)} 件` : `已运行 ${task.elapsed_seconds.toFixed(0)} 秒`}
          {task.continued_in_background && ' · 已转入后台，可以离开当前页面'}
        </span>
        {task.cancellable && <button type="button" onClick={onCancel}>取消任务</button>}
      </div>
      {task.cancellable && <small className="provider-cancel-note">停止本地执行和后续保存；无法撤销服务商已经接收的计算。</small>}
      {!!task.trace?.length && <details><summary>执行记录</summary>{task.trace.map((event,i)=><p key={i}>{event.name} · {event.status} · {(event.duration_ms/1000).toFixed(1)} 秒</p>)}</details>}
    </div>
  )
}

interface StepImageRecord { step_order: number; source: string; status: string; image_url?: string | null; error?: string | null; reason?: string }
function ResultView({ tutorialId, result, entry, referencePreview, products, onCreateAnother }: { tutorialId: string | null; result: PipelineResult | null; entry: DiaryEntry | null; referencePreview: string | null; products: ProductRecord[]; onCreateAnother: () => void }) {
  const productNames = new Map(products.map((item) => [item.id, `${item.enrichment?.canonical_brand ?? item.raw_brand} ${item.enrichment?.canonical_name ?? item.raw_name}`]))
  const [stepImages, setStepImages] = useState<StepImageRecord[]>([])
  const [retryStatus, setRetryStatus] = useState<{stage: 'steps' | 'products' | null; message: string}>({stage: null, message: ''})
  useEffect(() => {
    if (!tutorialId || !result) return
    let active = true
    const load = () => request<StepImageRecord[]>(`/api/tutorials/${tutorialId}/images`).then(items => { if (active) setStepImages(items) }).catch(() => {})
    void load()
    const timer = window.setInterval(load, 4000)
    return () => { active = false; window.clearInterval(timer) }
  }, [tutorialId, result])
  const generateStep = async (order: number) => {
    if (!tutorialId) return
    try {
      await request(`/api/tutorials/${tutorialId}/images/${order}`, {method:'POST'})
      setStepImages(await request(`/api/tutorials/${tutorialId}/images`))
    } catch (error) { setRetryStatus({stage: 'steps', message: String(error)}) }
  }
  const retry = async (stage: 'steps' | 'products') => {
    if (!tutorialId) return
    setRetryStatus({stage, message: '正在重新启动…'})
    try { await request(`/api/tutorials/${tutorialId}/retry?stage=${stage}`, {method:'POST'}); setRetryStatus({stage, message: '任务已开始'}) }
    catch (error) { setRetryStatus({stage, message: String(error)}) }
  }
  const style = result?.analysis.overall_style || entry?.analysis_result?.overall_style || []
  const stepTask = entry?.stages?.steps
  const productTask = entry?.stages?.products
  const productGroups = Object.values((entry?.products_result?.items || []).reduce<Record<string, NonNullable<DiaryEntry['products_result']>['items']>>((groups, item) => {
    const key = `${item.target_id}:${item.category}`
    ;(groups[key] ||= []).push(item)
    return groups
  }, {}))
  const matchRank = (status: TutorialStep['product_match']['status']) => ({owned_match: 0, owned_approximation: 1, catalog_candidate: 2, missing: 3, not_needed: 4, pending: 5})[status]
  return (
    <div className="result-page page-enter">
      {result?.analysis.provider.includes('fixture') && <p className="inline-warning">演示结果：固定示例，未对上传图片进行真实分析。</p>}
      <section className="result-hero">
        <div className="result-photo">{referencePreview && <img src={referencePreview} alt="参考妆面" />}<span>REFERENCE LOOK</span></div>
        <div className="result-title">
          <span className="eyebrow">YOUR PERSONALIZED ROUTINE</span>
          <h1>{result?.tutorial.title || '你的专属妆教'}</h1>
          <div className="tag-row">{style.map(label => <span key={label}>{label}</span>)}</div>
          {result && <div className="result-meta"><span><strong>{result.tutorial.steps.length}</strong> 个步骤</span></div>}
        </div>
      </section>
      <section className="tutorial-section">
        <div className="section-heading"><div><span className="eyebrow">STEP BY STEP</span><h2>复刻步骤</h2></div><button onClick={onCreateAnother}>分析另一张图片</button></div>
        {result ? <div className="timeline">{result.tutorial.steps.map(step => <TutorialStepCard key={step.step_id || step.order} step={step} image={stepImages.find(image => image.step_order === step.order)} onGenerate={() => void generateStep(step.order)} illustration={result.step_illustrations?.find(image => image.step_order === step.order)} productName={step.product_match.selected_product_id ? productNames.get(step.product_match.selected_product_id) : undefined} />)}</div> : <p>复刻步骤：{stageLabel(stepTask)}{stepTask?.error ? ` · ${stepTask.error.slice(0, 80)}` : ''}</p>}
        {stepTask && ['failed','timed_out','cancelled'].includes(stepTask.status) && <button onClick={() => void retry('steps')}>重试复刻步骤</button>}
        {retryStatus.stage === 'steps' && <small role="status">{retryStatus.message}</small>}
        {(stepTask?.trace?.length || stepTask?.error) ? <details><summary>步骤审查与执行记录</summary>{stepTask.error && <p>{stepTask.error}</p>}{stepTask.trace?.map((item,i) => <p key={i}>{item.name} · {item.status}</p>)}</details> : null}
      </section>
      <section className="tutorial-section product-result-section">
        <div className="section-heading"><div><span className="eyebrow">PRODUCT MATCH</span><h2>本次妆容产品清单</h2></div></div>
        {entry?.products_result ? <div className="product-result-list">{productGroups.map(group => {
          const ranked = [...group].sort((a,b) => matchRank(a.match.status) - matchRank(b.match.status))
          const item = ranked[0]
          const name = item.match.catalog_product_name || (item.match.selected_product_id ? productNames.get(item.match.selected_product_id) : null) || (item.match.status === 'missing' ? '尚无合适产品' : '无需准备')
          return <article className="product-result-item" key={`${item.target_id}-${item.category}`}><strong>{item.target_effect} · {categoryLabels[item.category] || item.category}</strong><p>{name}</p><small>{item.match.match_reason}</small>{item.match.catalog_source_url && <a href={item.match.catalog_source_url} target="_blank" rel="noreferrer">查看来源 ↗</a>}{ranked.length > 1 && <details><summary>查看其他技法候选（{ranked.length - 1}）</summary>{ranked.slice(1).map(other => <p key={other.card_id}>{other.match.catalog_product_name || (other.match.selected_product_id ? productNames.get(other.match.selected_product_id) : null) || '暂无合适产品'} · {other.match.match_reason}</p>)}</details>}</article>
        })}</div> : <p>产品匹配：{stageLabel(productTask)}{productTask?.error ? ` · ${productTask.error.slice(0, 80)}` : ''}</p>}
        {productTask && ['failed','timed_out','cancelled'].includes(productTask.status) && <button onClick={() => void retry('products')}>重试产品匹配</button>}
        {retryStatus.stage === 'products' && <small role="status">{retryStatus.message}</small>}
        {(productTask?.trace?.length || productTask?.error) ? <details><summary>产品核验与执行记录</summary>{productTask.error && <p>{productTask.error}</p>}{productTask.trace?.map((item,i) => <p key={i}>{item.name} · {item.status}</p>)}</details> : null}
      </section>
    </div>
  )
}

function TutorialStepCard({ step, illustration, image, onGenerate, productName }: { step: TutorialStep; illustration?: StepIllustration; image?: StepImageRecord; onGenerate: () => void; productName?: string }) {
  const displayGoal = step.goal.split(/[，。；]/)[0]
  const compact = (value: string) => {
    const first = value.trim().split(/[，；。]/)[0].trim()
    return first.length > 30 ? `${first.slice(0, 30)}…` : first
  }
  const action = step.application?.action
    .replace(/^.*?后再操作[：:]/, '')
    .replace(/^(?:取得|确认|核对).*?后[，,]/, '')
    .trim()
  return (
    <article className="tutorial-step">
      <div className="step-index">{String(step.order).padStart(2, '0')}</div>
      <div className="step-body">
        <div className="step-title"><span>{regionLabels[step.region] ?? step.region}</span><h3>{displayGoal}</h3></div>
        {image?.image_url && ['succeeded','queued','running'].includes(image.status) ? <div className="real-step-image"><img src={image.image_url} alt={`${step.goal}的教学示意`} /><small>AI 效果示意 · {image.source === 'local_effect' ? '当前步骤妆效' : '标准底图'}</small></div> : <StepIllustrationDiagram step={step} illustration={illustration} />}
        {!step.drawing && image?.source !== 'local_effect' && <small className="step-image-status">使用通用部位示意，操作落点以文字为准</small>}
        {image?.status === 'queued' || image?.status === 'running' ? <p className="step-image-status">妆效图生成中，文字步骤已可阅读</p> : null}
        {image?.status === 'failed' || image?.status === 'unavailable' ? <p className="step-image-status">{image.error || image.reason || '图片生成失败，显示 SVG 示意'}</p> : null}
        {image?.source !== 'local_effect' && <button type="button" className="step-image-generate" onClick={onGenerate} disabled={image?.status === 'queued' || image?.status === 'running'}>生成这一步妆效图</button>}
        {step.application ? <div className="step-application concise-actions">
          <p>1. {compact(step.application.amount)}，{compact(action ?? '')}。</p>
          <p>2. {compact(step.application.placement)}；{compact(step.application.direction)}。</p>
          <p>3. {compact(step.application.layers)}。</p>
        </div> : <p className="instruction">{step.instruction}</p>}
        <div className="success-signal"><span>完成标志</span><p>{compact(step.success_signal)}</p></div>
        <div className={`match-box ${step.product_match.status}`}><span>{step.product_match.status === 'pending' ? '正在匹配' : step.product_match.status === 'not_needed' ? '无需产品' : step.product_match.status === 'missing' ? '需要准备' : step.product_match.status === 'catalog_candidate' ? '目录建议' : '我的产品'}</span><p>{step.product_match.catalog_product_name ?? productName ?? categoryLabels[step.product_match.required_category] ?? step.product_match.required_category}{step.product_match.selected_color_label && ` · ${step.product_match.selected_color_label}`}{step.tool && ` · ${step.tool}`}</p>{step.product_match.catalog_source_url && <small><a href={step.product_match.catalog_source_url} target="_blank" rel="noopener noreferrer">查看产品来源 ↗</a></small>}</div>
        {step.product_match.selected_product_id && step.application && <p className="step-product-tip">专属用法：{step.product_match.status === 'owned_approximation' ? '核对实物颜色后，' : ''}少量取用{productName || categoryLabels[step.product_match.required_category]}，按这一步的{compact(step.application.placement)}、{compact(step.application.direction)}操作；{compact(step.application.completion_check)}。</p>}
        {step.technique_source_url && <p className="step-foot">相关教程：<a href={step.technique_source_url} target="_blank" rel="noopener noreferrer">{step.technique_source_title ?? step.technique_source_publisher ?? '查看技法'} ↗</a></p>}
        {step.technique_review_status === 'draft' && <small className="step-review-note">技法卡待人工复核</small>}
      </div>
    </article>
  )
}

interface DiaryEntry {
  id: string; image_url: string; provider: string; product_records: ProductRecord[]; task: BackgroundTask
  workflow_version?: number
  stages?: {analysis?: BackgroundTask | null; steps?: BackgroundTask | null; products?: BackgroundTask | null}
  analysis_result?: PipelineResult['analysis'] | null
  steps_result?: PipelineResult | null
  products_result?: {items: {target_id: string; target_effect: string; card_id: string; category: string; match: TutorialStep['product_match']}[]} | null
}

export default App
