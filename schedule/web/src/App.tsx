import { useCallback, useEffect, useMemo, useState } from "react";
import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin from "@fullcalendar/interaction";
import zhCnLocale from "@fullcalendar/core/locales/zh-cn";
import type { DatesSetArg, EventClickArg, EventInput } from "@fullcalendar/core";
import {
  Bell,
  Ban,
  CalendarPlus,
  CalendarDays,
  CheckCircle2,
  Clock,
  ExternalLink,
  Inbox,
  MessageSquareText,
  MapPin,
  Plus,
  RefreshCw,
  Repeat,
  Save,
  Trash2,
  X,
} from "lucide-react";

type Frequency = "daily" | "weekly" | "monthly" | "yearly";
type Weekday = "MO" | "TU" | "WE" | "TH" | "FR" | "SA" | "SU";
type Scope = "this" | "future" | "all";

type RecurrenceRule = {
  freq: Frequency;
  interval: number;
  until?: string | null;
  count?: number | null;
  weekdays?: Weekday[];
};

type ScheduleEvent = {
  id: string;
  event_id: string;
  parent_event_id?: string | null;
  title: string;
  start_at: string;
  end_at: string;
  all_day: boolean;
  category: string;
  location: string;
  notes: string;
  source: string;
  reminder_minutes?: number | null;
  recurrence?: RecurrenceRule | null;
  occurrence_start: string;
  is_recurring: boolean;
  is_exception: boolean;
};

type EventDraft = {
  title: string;
  date: string;
  startTime: string;
  endTime: string;
  allDay: boolean;
  category: string;
  location: string;
  notes: string;
  source: string;
  reminderMinutes: string;
  recurrenceFreq: "none" | Frequency;
  recurrenceInterval: string;
  recurrenceUntil: string;
  recurrenceCount: string;
  weekdays: Weekday[];
};

type XuexitongSyncResponse = {
  status: string;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  needs_login: boolean;
  pages_scanned: number;
  error: string;
  items: Array<{
    status: string;
    event_id?: string | null;
    title: string;
    external_key: string;
    error?: string;
  }>;
};

type QQCandidate = {
  id: string;
  event_id?: string | null;
  title: string;
  start_at?: string | null;
  end_at?: string | null;
  all_day: boolean;
  category: string;
  location: string;
  notes: string;
  reminder_minutes?: number | null;
  confidence: number;
  missing_fields: string[];
  parse_source: string;
  status: string;
  last_error: string;
  raw_result: Record<string, unknown>;
  updated_at: string;
};

type QQStatusResponse = {
  config: {
    enabled: boolean;
    config_path: string;
    auto_create_min_confidence: number;
    groups: Array<{
      group_name: string;
      course_name: string;
      teacher_count: number;
    }>;
  };
  counts: Record<string, number>;
  candidates: QQCandidate[];
};

type InboxItem = {
  id: string;
  provider: string;
  external_key: string;
  source_item_id: string;
  title: string;
  summary: string;
  category: string;
  start_at?: string | null;
  end_at?: string | null;
  all_day: boolean;
  location: string;
  source_name: string;
  source_url: string;
  action_url: string;
  status: string;
  event_id?: string | null;
  last_error: string;
  updated_at: string;
};

type InboxStatusResponse = {
  counts: Record<string, number>;
  recent: InboxItem[];
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const API_KEY = import.meta.env.VITE_SCHEDULE_API_KEY ?? "dev-schedule-key";
const CATEGORIES = ["课程", "考试", "作业", "会议", "生活", "项目", "其他"];
const WEEKDAYS: Array<{ value: Weekday; label: string }> = [
  { value: "MO", label: "一" },
  { value: "TU", label: "二" },
  { value: "WE", label: "三" },
  { value: "TH", label: "四" },
  { value: "FR", label: "五" },
  { value: "SA", label: "六" },
  { value: "SU", label: "日" },
];

const CATEGORY_COLORS: Record<string, string> = {
  课程: "#2563eb",
  考试: "#dc2626",
  作业: "#ca8a04",
  会议: "#0f766e",
  生活: "#16a34a",
  项目: "#7c3aed",
  志愿活动: "#db2777",
  其他: "#475569",
};

function chinaToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function dateParam(date: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function addDays(dateText: string, days: number): string {
  const date = new Date(`${dateText}T00:00:00+08:00`);
  date.setUTCDate(date.getUTCDate() + days);
  return dateParam(date);
}

function isoFromDateTime(date: string, time: string): string {
  return `${date}T${time || "00:00"}:00+08:00`;
}

function timeFromIso(value: string): string {
  return value.slice(11, 16);
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date(`${value}T00:00:00+08:00`));
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function emptyDraft(date = chinaToday()): EventDraft {
  return {
    title: "",
    date,
    startTime: "09:00",
    endTime: "10:00",
    allDay: false,
    category: "其他",
    location: "",
    notes: "",
    source: "manual",
    reminderMinutes: "",
    recurrenceFreq: "none",
    recurrenceInterval: "1",
    recurrenceUntil: "",
    recurrenceCount: "",
    weekdays: [],
  };
}

function draftFromEvent(item: ScheduleEvent): EventDraft {
  const recurrence = item.recurrence;
  return {
    title: item.title,
    date: item.start_at.slice(0, 10),
    startTime: timeFromIso(item.start_at),
    endTime: timeFromIso(item.end_at),
    allDay: item.all_day,
    category: item.category || "其他",
    location: item.location || "",
    notes: item.notes || "",
    source: item.source || "manual",
    reminderMinutes: item.reminder_minutes == null ? "" : String(item.reminder_minutes),
    recurrenceFreq: recurrence?.freq ?? "none",
    recurrenceInterval: String(recurrence?.interval ?? 1),
    recurrenceUntil: recurrence?.until ?? "",
    recurrenceCount: recurrence?.count == null ? "" : String(recurrence.count),
    weekdays: recurrence?.weekdays ?? [],
  };
}

function draftFromQqCandidate(item: QQCandidate, fallbackDate: string): EventDraft {
  const date = item.start_at?.slice(0, 10) || fallbackDate;
  return {
    title: item.title,
    date,
    startTime: item.start_at ? timeFromIso(item.start_at) : "09:00",
    endTime: item.end_at ? timeFromIso(item.end_at) : "10:00",
    allDay: item.all_day,
    category: item.category || "课程",
    location: item.location || "",
    notes: item.notes || "",
    source: "qq",
    reminderMinutes: item.reminder_minutes == null ? "" : String(item.reminder_minutes),
    recurrenceFreq: "none",
    recurrenceInterval: "1",
    recurrenceUntil: "",
    recurrenceCount: "",
    weekdays: [],
  };
}

function draftFromInboxItem(item: InboxItem, fallbackDate: string): EventDraft {
  const date = item.start_at?.slice(0, 10) || fallbackDate;
  return {
    title: item.title,
    date,
    startTime: item.start_at ? timeFromIso(item.start_at) : "09:00",
    endTime: item.end_at ? timeFromIso(item.end_at) : "10:00",
    allDay: item.all_day,
    category: item.category || "项目",
    location: item.location || "",
    notes: item.summary || "",
    source: item.provider,
    reminderMinutes: "60",
    recurrenceFreq: "none",
    recurrenceInterval: "1",
    recurrenceUntil: "",
    recurrenceCount: "",
    weekdays: [],
  };
}

function buildPayload(draft: EventDraft) {
  const start_at = draft.allDay
    ? isoFromDateTime(draft.date, "00:00")
    : isoFromDateTime(draft.date, draft.startTime);
  const end_at = draft.allDay
    ? isoFromDateTime(addDays(draft.date, 1), "00:00")
    : isoFromDateTime(draft.date, draft.endTime);

  const recurrence =
    draft.recurrenceFreq === "none"
      ? null
      : {
          freq: draft.recurrenceFreq,
          interval: Number(draft.recurrenceInterval || 1),
          until: draft.recurrenceUntil || null,
          count: draft.recurrenceCount ? Number(draft.recurrenceCount) : null,
          weekdays: draft.recurrenceFreq === "weekly" ? draft.weekdays : [],
        };

  return {
    title: draft.title.trim(),
    start_at,
    end_at,
    all_day: draft.allDay,
    category: draft.category.trim() || "其他",
    location: draft.location.trim(),
    notes: draft.notes.trim(),
    source: draft.source.trim() || "manual",
    reminder_minutes: draft.reminderMinutes ? Number(draft.reminderMinutes) : null,
    recurrence,
  };
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) {
    headers.set("Content-Type", "application/json");
  }
  if (options.method && options.method !== "GET") {
    headers.set("X-API-Key", API_KEY);
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败：${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? "#475569";
}

function eventOnDate(item: ScheduleEvent, selectedDate: string): boolean {
  const start = new Date(item.start_at);
  const end = new Date(item.end_at);
  const dayStart = new Date(`${selectedDate}T00:00:00+08:00`);
  const dayEnd = new Date(dayStart);
  dayEnd.setUTCDate(dayEnd.getUTCDate() + 1);
  return start < dayEnd && end > dayStart;
}

function qqStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    created: "已写入",
    pending: "待确认",
    ready: "待写入",
    failed: "失败",
    skipped_deleted: "已删过",
  };
  return labels[status] ?? status;
}

function inboxStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "待处理",
    calendar_added: "已入日程",
    joined: "已参加",
    later: "稍后",
    ignored: "已忽略",
    needs_attention: "需处理",
    failed: "失败",
  };
  return labels[status] ?? status;
}

export default function App() {
  const [events, setEvents] = useState<ScheduleEvent[]>([]);
  const [selectedDate, setSelectedDate] = useState(chinaToday());
  const [draft, setDraft] = useState<EventDraft>(() => emptyDraft());
  const [activeEvent, setActiveEvent] = useState<ScheduleEvent | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [scope, setScope] = useState<Scope>("this");
  const [range, setRange] = useState({ from: chinaToday(), to: chinaToday() });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncingXuexitong, setSyncingXuexitong] = useState(false);
  const [loadingQq, setLoadingQq] = useState(false);
  const [qqStatus, setQqStatus] = useState<QQStatusResponse | null>(null);
  const [qqCandidates, setQqCandidates] = useState<QQCandidate[]>([]);
  const [confirmingQqCandidate, setConfirmingQqCandidate] = useState<QQCandidate | null>(null);
  const [loadingInbox, setLoadingInbox] = useState(false);
  const [inboxStatus, setInboxStatus] = useState<InboxStatusResponse | null>(null);
  const [inboxItems, setInboxItems] = useState<InboxItem[]>([]);
  const [confirmingInboxItem, setConfirmingInboxItem] = useState<InboxItem | null>(null);
  const [statusText, setStatusText] = useState("");
  const [statusTone, setStatusTone] = useState<"error" | "success">("error");

  const loadEvents = useCallback(async (from: string, to: string) => {
    setLoading(true);
    setStatusText("");
    try {
      const items = await api<ScheduleEvent[]>(`/api/events?from=${from}&to=${to}`);
      setEvents(items);
    } catch (error) {
      setStatusTone("error");
      setStatusText(error instanceof Error ? error.message : "日程加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadQqStatus = useCallback(async () => {
    setLoadingQq(true);
    try {
      const result = await api<QQStatusResponse>("/api/integrations/qq/status");
      setQqStatus(result);
      setQqCandidates(result.candidates);
    } catch (error) {
      setStatusTone("error");
      setStatusText(error instanceof Error ? error.message : "QQ群消息状态加载失败");
    } finally {
      setLoadingQq(false);
    }
  }, []);

  const loadInbox = useCallback(async () => {
    setLoadingInbox(true);
    try {
      const [statusResult, itemResult] = await Promise.all([
        api<InboxStatusResponse>("/api/inbox/status"),
        api<InboxItem[]>("/api/inbox/items?limit=50"),
      ]);
      setInboxStatus(statusResult);
      setInboxItems(itemResult.filter((item) => item.status !== "ignored"));
    } catch (error) {
      setStatusTone("error");
      setStatusText(error instanceof Error ? error.message : "待办收件箱加载失败");
    } finally {
      setLoadingInbox(false);
    }
  }, []);

  useEffect(() => {
    void loadQqStatus();
    void loadInbox();
  }, [loadInbox, loadQqStatus]);

  const calendarEvents = useMemo<EventInput[]>(
    () =>
      events.map((item) => ({
        id: item.id,
        title: item.title,
        start: item.start_at,
        end: item.end_at,
        allDay: item.all_day,
        backgroundColor: categoryColor(item.category),
        borderColor: categoryColor(item.category),
        extendedProps: item,
      })),
    [events],
  );

  const selectedEvents = useMemo(
    () =>
      events
        .filter((item) => eventOnDate(item, selectedDate))
        .sort((a, b) => a.start_at.localeCompare(b.start_at)),
    [events, selectedDate],
  );

  function handleDatesSet(arg: DatesSetArg) {
    const from = dateParam(arg.start);
    const inclusiveEnd = new Date(arg.end);
    inclusiveEnd.setUTCDate(inclusiveEnd.getUTCDate() - 1);
    const to = dateParam(inclusiveEnd);
    setRange({ from, to });
    void loadEvents(from, to);
  }

  function openCreate(date = selectedDate) {
    setActiveEvent(null);
    setConfirmingQqCandidate(null);
    setConfirmingInboxItem(null);
    setDraft(emptyDraft(date));
    setScope("all");
    setModalOpen(true);
  }

  function openEdit(item: ScheduleEvent) {
    setActiveEvent(item);
    setConfirmingQqCandidate(null);
    setConfirmingInboxItem(null);
    setDraft(draftFromEvent(item));
    setSelectedDate(item.start_at.slice(0, 10));
    setScope(item.is_recurring ? "this" : "all");
    setModalOpen(true);
  }

  function openQqCandidate(item: QQCandidate) {
    setActiveEvent(null);
    setConfirmingQqCandidate(item);
    setConfirmingInboxItem(null);
    setDraft(draftFromQqCandidate(item, selectedDate));
    setScope("all");
    setModalOpen(true);
  }

  function openInboxItem(item: InboxItem) {
    setActiveEvent(null);
    setConfirmingQqCandidate(null);
    setConfirmingInboxItem(item);
    setDraft(draftFromInboxItem(item, selectedDate));
    setScope("all");
    setModalOpen(true);
  }

  function toggleWeekday(day: Weekday) {
    setDraft((current) => ({
      ...current,
      weekdays: current.weekdays.includes(day)
        ? current.weekdays.filter((item) => item !== day)
        : [...current.weekdays, day],
    }));
  }

  async function handleSave() {
    if (!draft.title.trim()) {
      setStatusTone("error");
      setStatusText("标题不能为空");
      return;
    }
    const payload = buildPayload(draft);
    if (new Date(payload.end_at) <= new Date(payload.start_at)) {
      setStatusTone("error");
      setStatusText("结束时间必须晚于开始时间");
      return;
    }
    setSaving(true);
    setStatusText("");
    try {
      if (!activeEvent) {
        if (confirmingInboxItem) {
          await api(`/api/inbox/items/${confirmingInboxItem.id}/decision`, {
            method: "POST",
            body: JSON.stringify({ action: "add_calendar", updates: payload }),
          });
          await loadInbox();
        } else if (confirmingQqCandidate) {
          await api(`/api/integrations/qq/candidates/${confirmingQqCandidate.id}/confirm`, {
            method: "POST",
            body: JSON.stringify({ updates: payload }),
          });
          await loadQqStatus();
        } else {
          await api<ScheduleEvent>("/api/events", {
            method: "POST",
            body: JSON.stringify(payload),
          });
        }
      } else if (activeEvent.is_recurring) {
        const updates = scope === "this" ? { ...payload, recurrence: undefined } : payload;
        await api(`/api/events/${activeEvent.event_id}/occurrences/modify`, {
          method: "POST",
          body: JSON.stringify({
            occurrence_start: activeEvent.occurrence_start,
            scope,
            updates,
          }),
        });
      } else {
        await api<ScheduleEvent>(`/api/events/${activeEvent.event_id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      }
      setModalOpen(false);
      setConfirmingQqCandidate(null);
      setConfirmingInboxItem(null);
      setSelectedDate(draft.date);
      await loadEvents(range.from, range.to);
    } catch (error) {
      setStatusTone("error");
      setStatusText(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!activeEvent) return;
    setSaving(true);
    setStatusText("");
    try {
      if (activeEvent.is_recurring) {
        await api(`/api/events/${activeEvent.event_id}/occurrences/delete`, {
          method: "POST",
          body: JSON.stringify({
            occurrence_start: activeEvent.occurrence_start,
            scope,
          }),
        });
      } else {
        await api(`/api/events/${activeEvent.event_id}`, { method: "DELETE" });
      }
      setModalOpen(false);
      await loadEvents(range.from, range.to);
    } catch (error) {
      setStatusTone("error");
      setStatusText(error instanceof Error ? error.message : "删除失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleXuexitongSync() {
    setSyncingXuexitong(true);
    setStatusText("");
    try {
      const result = await api<XuexitongSyncResponse>("/api/integrations/xuexitong/sync", {
        method: "POST",
      });

      if (result.status === "chrome_unavailable") {
        setStatusTone("error");
        setStatusText("请先运行 .\\start_xuexitong_chrome.ps1，并在专用 Chrome 窗口登录学习通");
        return;
      }
      if (result.status === "dependency_missing") {
        setStatusTone("error");
        setStatusText(result.error || "后端缺少 Playwright 依赖，请重新运行 .\\start.ps1 安装依赖");
        return;
      }
      if (result.needs_login) {
        setStatusTone("error");
        setStatusText("需要先在专用 Chrome 调试窗口里登录学习通");
        return;
      }
      if (result.status === "browser_error") {
        setStatusTone("error");
        setStatusText(result.error || "读取学习通页面失败");
        return;
      }

      await loadEvents(range.from, range.to);
      setStatusTone(result.failed ? "error" : "success");
      setStatusText(
        `学习通同步完成：新建 ${result.created}，更新 ${result.updated}，跳过 ${result.skipped}，失败 ${result.failed}`,
      );
    } catch (error) {
      setStatusTone("error");
      setStatusText(error instanceof Error ? error.message : "学习通同步失败");
    } finally {
      setSyncingXuexitong(false);
    }
  }

  async function handleQqRefresh() {
    await loadQqStatus();
    setStatusTone("success");
    setStatusText("QQ群消息状态已刷新");
  }

  async function handleInboxDecision(item: InboxItem, action: "join" | "later" | "ignore") {
    setLoadingInbox(true);
    setStatusText("");
    try {
      const result = await api<{ status: string; callback?: { message?: string } }>(
        `/api/inbox/items/${item.id}/decision`,
        {
          method: "POST",
          body: JSON.stringify({ action, updates: {} }),
        },
      );
      await Promise.all([loadInbox(), loadEvents(range.from, range.to)]);
      setStatusTone(result.status === "failed" || result.status === "needs_attention" ? "error" : "success");
      setStatusText(
        result.status === "joined"
          ? "已创建报名任务并同步处理结果"
          : result.status === "later"
            ? "已移到稍后处理"
            : result.status === "ignored"
              ? "已忽略这条待办"
              : result.callback?.message || "待办处理失败",
      );
    } catch (error) {
      setStatusTone("error");
      setStatusText(error instanceof Error ? error.message : "待办处理失败");
    } finally {
      setLoadingInbox(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-icon" aria-hidden="true">
            <CalendarDays size={24} />
          </div>
          <div>
            <h1>本地日程安排</h1>
            <p>{formatDate(selectedDate)}</p>
          </div>
        </div>
        <div className="topbar-actions">
          <button
            className="secondary-button"
            type="button"
            title="同步学习通"
            onClick={handleXuexitongSync}
            disabled={syncingXuexitong}
          >
            <RefreshCw size={18} className={syncingXuexitong ? "spin-icon" : ""} />
            {syncingXuexitong ? "同步中" : "同步学习通"}
          </button>
          <button
            className="secondary-button"
            type="button"
            title="刷新待办收件箱"
            onClick={loadInbox}
            disabled={loadingInbox}
          >
            <Inbox size={18} />
            待办 {inboxStatus?.counts.pending ?? 0}
          </button>
          <button
            className="secondary-button"
            type="button"
            title="刷新QQ群消息"
            onClick={handleQqRefresh}
            disabled={loadingQq}
          >
            <MessageSquareText size={18} />
            QQ群消息
          </button>
          <button
            className="icon-button ghost"
            type="button"
            title="刷新"
            aria-label="刷新"
            onClick={() => loadEvents(range.from, range.to)}
          >
            <RefreshCw size={18} />
          </button>
          <button className="primary-button" type="button" onClick={() => openCreate()}>
            <Plus size={18} />
            添加
          </button>
        </div>
      </header>

      <section className="workspace">
        <div className="calendar-panel">
          <FullCalendar
            plugins={[dayGridPlugin, interactionPlugin]}
            locale={zhCnLocale}
            initialView="dayGridMonth"
            height="100%"
            firstDay={1}
            fixedWeekCount={false}
            dayMaxEvents={3}
            events={calendarEvents}
            datesSet={handleDatesSet}
            dateClick={(arg) => {
              setSelectedDate(arg.dateStr);
            }}
            eventClick={(arg: EventClickArg) => {
              openEdit(arg.event.extendedProps as ScheduleEvent);
            }}
            headerToolbar={{
              left: "prev,next today",
              center: "title",
              right: "",
            }}
            buttonText={{
              today: "今天",
            }}
          />
        </div>

        <aside className="day-panel">
          <div className="day-panel-header">
            <div>
              <span className="eyebrow">当天安排</span>
              <h2>{formatDate(selectedDate)}</h2>
            </div>
            <button
              className="icon-button"
              type="button"
              title="给这天添加"
              aria-label="给这天添加"
              onClick={() => openCreate(selectedDate)}
            >
              <Plus size={18} />
            </button>
          </div>

          {loading ? <div className="empty-state">加载中...</div> : null}
          {!loading && selectedEvents.length === 0 ? (
            <div className="empty-state">这一天还没有安排</div>
          ) : null}
          <div className="event-list">
            {selectedEvents.map((item) => (
              <button
                className="event-row"
                type="button"
                key={item.id}
                onClick={() => openEdit(item)}
              >
                <span
                  className="event-color"
                  style={{ backgroundColor: categoryColor(item.category) }}
                />
                <span className="event-main">
                  <strong>{item.title}</strong>
                  <span>
                    {item.all_day ? "全天" : `${formatTime(item.start_at)} - ${formatTime(item.end_at)}`}
                  </span>
                </span>
                {item.is_recurring ? <Repeat size={16} aria-label="重复日程" /> : null}
              </button>
            ))}
          </div>

          <section className="inbox-panel">
            <div className="qq-panel-header">
              <div>
                <span className="eyebrow">统一入口</span>
                <h3>待办收件箱</h3>
              </div>
              <button
                className="icon-button ghost"
                type="button"
                title="刷新待办"
                aria-label="刷新待办"
                onClick={loadInbox}
                disabled={loadingInbox}
              >
                <RefreshCw size={17} className={loadingInbox ? "spin-icon" : ""} />
              </button>
            </div>

            <div className="qq-summary">
              <span>待处理 {inboxStatus?.counts.pending ?? 0}</span>
              <span>已参加 {inboxStatus?.counts.joined ?? 0}</span>
              <span>需处理 {inboxStatus?.counts.needs_attention ?? 0}</span>
            </div>

            {inboxItems.length === 0 ? (
              <div className="qq-empty">还没有公众号待办</div>
            ) : (
              <div className="qq-candidate-list">
                {inboxItems.slice(0, 8).map((item) => (
                  <article className="qq-candidate inbox-item" key={item.id}>
                    <div className="qq-candidate-title">
                      <div className="inbox-title-block">
                        <span className="source-badge">{item.source_name || "公众号监测"}</span>
                        <strong>{item.title}</strong>
                      </div>
                      <span className={`qq-status ${item.status}`}>{inboxStatusLabel(item.status)}</span>
                    </div>
                    <div className="qq-meta">
                      <span>
                        {item.start_at
                          ? item.all_day
                            ? item.start_at.slice(0, 10)
                            : `${item.start_at.slice(0, 10)} ${timeFromIso(item.start_at)}`
                          : "时间待补"}
                      </span>
                      {item.location ? <span>{item.location}</span> : null}
                    </div>
                    {item.last_error ? <p className="qq-error">{item.last_error}</p> : null}
                    <div className="inbox-actions">
                      {item.status !== "joined" ? (
                        <button
                          className="primary-button compact"
                          type="button"
                          onClick={() => handleInboxDecision(item, "join")}
                          disabled={loadingInbox}
                        >
                          参加并报名
                        </button>
                      ) : (
                        <span className="qq-created">
                          <CheckCircle2 size={15} />
                          已参加
                        </span>
                      )}
                      {!item.event_id ? (
                        <button
                          className="icon-button compact"
                          type="button"
                          title="编辑并加入日程"
                          aria-label="编辑并加入日程"
                          onClick={() => openInboxItem(item)}
                        >
                          <CalendarPlus size={16} />
                        </button>
                      ) : null}
                      {item.status === "pending" ? (
                        <button
                          className="icon-button compact"
                          type="button"
                          title="稍后处理"
                          aria-label="稍后处理"
                          onClick={() => handleInboxDecision(item, "later")}
                        >
                          <Clock size={16} />
                        </button>
                      ) : null}
                      <button
                        className="icon-button compact"
                        type="button"
                        title="忽略"
                        aria-label="忽略"
                        onClick={() => handleInboxDecision(item, "ignore")}
                      >
                        <Ban size={16} />
                      </button>
                      {item.source_url ? (
                        <a
                          className="icon-link"
                          href={item.source_url}
                          target="_blank"
                          rel="noreferrer"
                          title="打开原文"
                          aria-label="打开原文"
                        >
                          <ExternalLink size={16} />
                        </a>
                      ) : null}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="qq-panel">
            <div className="qq-panel-header">
              <div>
                <span className="eyebrow">QQ群消息</span>
                <h3>老师通知候选</h3>
              </div>
              <button
                className="icon-button ghost"
                type="button"
                title="刷新QQ群消息"
                aria-label="刷新QQ群消息"
                onClick={handleQqRefresh}
                disabled={loadingQq}
              >
                <RefreshCw size={17} className={loadingQq ? "spin-icon" : ""} />
              </button>
            </div>

            <div className="qq-summary">
              <span>群 {qqStatus?.config.groups.length ?? 0}</span>
              <span>待确认 {qqStatus?.counts.pending ?? 0}</span>
              <span>已写入 {qqStatus?.counts.created ?? 0}</span>
            </div>

            {!qqStatus ? (
              <div className="qq-empty">点击刷新查看监听结果</div>
            ) : qqCandidates.length === 0 ? (
              <div className="qq-empty">还没有老师消息候选</div>
            ) : (
              <div className="qq-candidate-list">
                {qqCandidates.map((item) => (
                  <article className="qq-candidate" key={item.id}>
                    <div className="qq-candidate-title">
                      <strong>{item.title}</strong>
                      <span className={`qq-status ${item.status}`}>{qqStatusLabel(item.status)}</span>
                    </div>
                    <div className="qq-meta">
                      <span>{item.start_at ? (item.all_day ? item.start_at.slice(0, 10) : `${item.start_at.slice(0, 10)} ${timeFromIso(item.start_at)}`) : "时间待补"}</span>
                      <span>置信度 {Math.round(item.confidence * 100)}%</span>
                    </div>
                    {item.last_error ? <p className="qq-error">{item.last_error}</p> : null}
                    <div className="qq-actions">
                      {item.status === "created" && item.event_id ? (
                        <span className="qq-created">
                          <CheckCircle2 size={15} />
                          已在月历中
                        </span>
                      ) : (
                        <button
                          className="secondary-button compact"
                          type="button"
                          onClick={() => openQqCandidate(item)}
                        >
                          编辑确认
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        </aside>
      </section>

      {statusText ? <div className={`toast ${statusTone}`}>{statusText}</div> : null}

      {modalOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="event-title">
            <div className="modal-header">
              <h2 id="event-title">
                {activeEvent
                  ? "编辑日程"
                  : confirmingInboxItem
                    ? "确认待办日程"
                    : confirmingQqCandidate
                      ? "确认QQ群日程"
                      : "添加日程"}
              </h2>
              <button
                className="icon-button ghost"
                type="button"
                title="关闭"
                aria-label="关闭"
                onClick={() => setModalOpen(false)}
              >
                <X size={18} />
              </button>
            </div>

            <div className="form-grid">
              <label className="field full">
                <span>标题</span>
                <input
                  value={draft.title}
                  onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                  placeholder="例如：数据库课小组会"
                />
              </label>

              <label className="field">
                <span>日期</span>
                <input
                  type="date"
                  value={draft.date}
                  onChange={(event) => setDraft({ ...draft, date: event.target.value })}
                />
              </label>

              <label className="switch-row">
                <input
                  type="checkbox"
                  checked={draft.allDay}
                  onChange={(event) => setDraft({ ...draft, allDay: event.target.checked })}
                />
                <span>全天</span>
              </label>

              {!draft.allDay ? (
                <>
                  <label className="field">
                    <span>开始</span>
                    <input
                      type="time"
                      value={draft.startTime}
                      onChange={(event) => setDraft({ ...draft, startTime: event.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span>结束</span>
                    <input
                      type="time"
                      value={draft.endTime}
                      onChange={(event) => setDraft({ ...draft, endTime: event.target.value })}
                    />
                  </label>
                </>
              ) : null}

              <label className="field">
                <span>分类</span>
                <input
                  list="category-options"
                  value={draft.category}
                  onChange={(event) => setDraft({ ...draft, category: event.target.value })}
                />
                <datalist id="category-options">
                  {CATEGORIES.map((category) => (
                    <option key={category} value={category} />
                  ))}
                </datalist>
              </label>

              <label className="field">
                <span>
                  <MapPin size={15} /> 地点
                </span>
                <input
                  value={draft.location}
                  onChange={(event) => setDraft({ ...draft, location: event.target.value })}
                  placeholder="教室、图书馆、线上"
                />
              </label>

              <label className="field">
                <span>
                  <Bell size={15} /> 提醒
                </span>
                <input
                  type="number"
                  min="0"
                  max="10080"
                  value={draft.reminderMinutes}
                  onChange={(event) => setDraft({ ...draft, reminderMinutes: event.target.value })}
                  placeholder="提前分钟"
                />
              </label>

              <label className="field">
                <span>来源</span>
                <input
                  value={draft.source}
                  onChange={(event) => setDraft({ ...draft, source: event.target.value })}
                />
              </label>

              <fieldset className="repeat-box full">
                <legend>
                  <Repeat size={15} /> 重复
                </legend>
                <div className="repeat-grid">
                  <label className="field">
                    <span>频率</span>
                    <select
                      value={draft.recurrenceFreq}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          recurrenceFreq: event.target.value as EventDraft["recurrenceFreq"],
                        })
                      }
                    >
                      <option value="none">不重复</option>
                      <option value="daily">每天</option>
                      <option value="weekly">每周</option>
                      <option value="monthly">每月</option>
                      <option value="yearly">每年</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>间隔</span>
                    <input
                      type="number"
                      min="1"
                      max="365"
                      value={draft.recurrenceInterval}
                      onChange={(event) =>
                        setDraft({ ...draft, recurrenceInterval: event.target.value })
                      }
                    />
                  </label>
                  <label className="field">
                    <span>截止</span>
                    <input
                      type="date"
                      value={draft.recurrenceUntil}
                      onChange={(event) =>
                        setDraft({ ...draft, recurrenceUntil: event.target.value })
                      }
                    />
                  </label>
                  <label className="field">
                    <span>次数</span>
                    <input
                      type="number"
                      min="1"
                      max="5000"
                      value={draft.recurrenceCount}
                      onChange={(event) =>
                        setDraft({ ...draft, recurrenceCount: event.target.value })
                      }
                    />
                  </label>
                </div>
                {draft.recurrenceFreq === "weekly" ? (
                  <div className="weekday-row">
                    {WEEKDAYS.map((day) => (
                      <button
                        type="button"
                        key={day.value}
                        className={draft.weekdays.includes(day.value) ? "weekday active" : "weekday"}
                        onClick={() => toggleWeekday(day.value)}
                      >
                        {day.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </fieldset>

              <label className="field full">
                <span>备注</span>
                <textarea
                  rows={4}
                  value={draft.notes}
                  onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
                  placeholder="要带的材料、链接、注意事项"
                />
              </label>

              {activeEvent?.is_recurring ? (
                <fieldset className="scope-box full">
                  <legend>
                    <Clock size={15} /> 编辑范围
                  </legend>
                  <label>
                    <input
                      type="radio"
                      checked={scope === "this"}
                      onChange={() => setScope("this")}
                    />
                    本次
                  </label>
                  <label>
                    <input
                      type="radio"
                      checked={scope === "future"}
                      onChange={() => setScope("future")}
                    />
                    以后
                  </label>
                  <label>
                    <input
                      type="radio"
                      checked={scope === "all"}
                      onChange={() => setScope("all")}
                    />
                    全部
                  </label>
                </fieldset>
              ) : null}
            </div>

            <footer className="modal-actions">
              {activeEvent ? (
                <button className="danger-button" type="button" onClick={handleDelete} disabled={saving}>
                  <Trash2 size={17} />
                  删除
                </button>
              ) : (
                <span />
              )}
              <div className="modal-action-group">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setModalOpen(false)}
                  disabled={saving}
                >
                  取消
                </button>
                <button className="primary-button" type="button" onClick={handleSave} disabled={saving}>
                  <Save size={17} />
                  {saving
                    ? "保存中"
                    : confirmingInboxItem || confirmingQqCandidate
                      ? "确认写入"
                      : "保存"}
                </button>
              </div>
            </footer>
          </section>
        </div>
      ) : null}
    </main>
  );
}
