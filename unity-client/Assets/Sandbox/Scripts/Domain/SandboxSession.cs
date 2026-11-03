using System;
using System.Collections.Generic;
using System.Linq;

namespace Qiongtu.Sandbox
{
    /// <summary>客户端观察角色；本地切换仅用于阶段一交互验收。</summary>
    public enum SandboxRole { Red, Blue, Host }

    /// <summary>当前页面支持的三类虚拟单位。</summary>
    public enum SandboxUnitType { Recon, Ground, Support }

    /// <summary>前端建议的处理状态，不代表动作已执行。</summary>
    public enum SandboxPlanStatus { Pending, Approved, Rejected }

    /// <summary>一次单位显示数据；坐标是固定演示场景的平面坐标。</summary>
    [Serializable]
    public sealed class SandboxUnitData
    {
        public string id;
        public string name;
        public SandboxRole side;
        public SandboxUnitType type;
        public float x;
        public float z;
        public string status;

        /// <summary>创建无共享引用的单位显示副本。</summary>
        /// <returns>可供角色投影使用的新单位对象。</returns>
        public SandboxUnitData Copy() => (SandboxUnitData)MemberwiseClone();
    }

    /// <summary>一份可编辑的演示建议，保留其所属方和版本。</summary>
    [Serializable]
    public sealed class SandboxPlanData
    {
        public SandboxRole side;
        public int version = 1;
        public SandboxPlanStatus status;
        public string priority;
        public int resourceAllowance = 2;
        public string reconAssignment;
        public string groundAssignment;
        public string supportAssignment;

        /// <summary>创建独立草稿，编辑草稿不会提前采用建议。</summary>
        /// <returns>当前建议的独立副本。</returns>
        public SandboxPlanData Copy() => (SandboxPlanData)MemberwiseClone();
    }

    /// <summary>时间线中的前端操作事实，不能替代服务端执行收据。</summary>
    [Serializable]
    public sealed class SandboxRecord
    {
        public string id;
        public int step;
        public string title;
        public string detail;
        public SandboxRole side;
        public bool isPublic;
        public bool isPrivatePlan;

        /// <summary>创建用于投影和存档的独立记录。</summary>
        /// <returns>没有共享可写字段的副本。</returns>
        public SandboxRecord Copy() => (SandboxRecord)MemberwiseClone();
    }

    /// <summary>阶段一浏览器原型之外独立维护的 Unity 本地演示状态。</summary>
    [Serializable]
    public sealed class SandboxSessionData
    {
        public int schemaVersion = 1;
        public string sessionId;
        public string title;
        public int step;
        public int revision = 1;
        public string taskType;
        public SandboxUnitData[] units;
        public SandboxPlanData redPlan;
        public SandboxPlanData bluePlan;
        public List<SandboxRecord> records = new List<SandboxRecord>();
    }

    /// <summary>交给画面和 HUD 的角色投影，不含对方隐藏单位或建议。</summary>
    public sealed class SandboxProjection
    {
        public SandboxRole role;
        public string title;
        public int step;
        public int revision;
        public string taskType;
        public SandboxUnitData[] units;
        public SandboxPlanData ownPlan;
        public string waitingSummary;
        public SandboxRecord[] records;
    }

    /// <summary>
    /// 统一的客户端数据来源契约。后续真实适配器必须在服务端完成权限过滤，
    /// 本地演示实现不构成身份认证或正式规则引擎。
    /// </summary>
    public interface ISandboxViewSource
    {
        /// <summary>获取当前角色允许显示的演示数据。</summary>
        /// <param name="role">当前观察角色。</param>
        /// <returns>完整且版本一致的页面投影。</returns>
        SandboxProjection GetProjection(SandboxRole role);
    }

    /// <summary>固定虚构场景的数据适配器；仅承载前端操作和预置帧展示。</summary>
    public sealed partial class DemoSandboxViewSource : ISandboxViewSource
    {
        public static readonly string[] TaskTypes = { "区域巡查", "物资护送", "关键设施保护", "受损设备撤离" };
        public static readonly string[] EventTypes = { "设备故障", "通信中断", "天气变化", "任务变化" };
        private SandboxSessionData session;

        /// <summary>创建带六个初始单位和双方待确认建议的演示。</summary>
        public DemoSandboxViewSource()
        {
            session = CreateInitialSession();
        }

        /// <summary>返回当前可序列化状态的独立副本，不开放内部可写对象。</summary>
        public SandboxSessionData Current => CopySession(session);

        /// <summary>说明数据来源，避免把固定样例当作真实仿真。</summary>
        public string SourceLabel => "本地演示数据 · 未连接仿真服务";

        /// <summary>将内部样例状态投影为指定角色的画面数据。</summary>
        /// <param name="role">当前角色。</param>
        /// <returns>经过显示范围过滤的独立单位和建议副本。</returns>
        public SandboxProjection GetProjection(SandboxRole role)
        {
            if (!Enum.IsDefined(typeof(SandboxRole), role)) throw new ArgumentOutOfRangeException(nameof(role), "未知的观察角色");
            return new SandboxProjection
            {
                role = role,
                title = session.title,
                step = session.step,
                revision = session.revision,
                taskType = session.taskType,
                units = session.units.Where(unit => role == SandboxRole.Host || unit.side == role).Select(unit => unit.Copy()).ToArray(),
                ownPlan = role == SandboxRole.Host ? null : GetPlan(role).Copy(),
                waitingSummary = GetWaitingSummary(),
                records = session.records.Where(record => record.isPublic || record.side == role || role == SandboxRole.Host && !record.isPrivatePlan).Select(record => record.Copy()).ToArray()
            };
        }

        /// <summary>核对角色和双方确认状态后推进一个预置演示帧。</summary>
        /// <param name="role">请求推进的角色。</param>
        /// <param name="message">阻塞原因或操作说明。</param>
        /// <returns>本次是否改变演示步。</returns>
        public bool TryAdvance(SandboxRole role, out string message)
        {
            message = GetAdvanceBlocker(role);
            if (message.Length != 0) return false;
            session.step++;
            session.revision++;
            // 位移仅用于观察三维跟随效果，不进行寻路、任务或交战结算。
            foreach (SandboxUnitData unit in session.units)
            {
                if (unit.type == SandboxUnitType.Ground)
                    unit.x = Math.Max(0, Math.Min(120, unit.x + (unit.side == SandboxRole.Red ? 0.45f : -0.45f)));
            }
            message = session.step == 60 ? "演示已到截止步；正式任务结果与评分待后端结算" : "已切换至下一演示帧";
            return true;
        }

        /// <summary>返回推进按钮禁用原因，统一约束单步和播放。</summary>
        /// <param name="role">当前角色。</param>
        /// <returns>空字符串表示当前可推进。</returns>
        public string GetAdvanceBlocker(SandboxRole role)
        {
            if (role != SandboxRole.Host) return "时间由主持人控制";
            if (session.step >= 60) return "演示已到第 60 步";
            string waiting = GetWaitingSummary();
            return waiting.Length == 0 ? string.Empty : "等待" + waiting + "处理建议";
        }

        /// <summary>采用当前角色的有效草稿，保留采用与实际执行的区别。</summary>
        /// <param name="role">处理建议的角色。</param>
        /// <param name="draft">带原版本的本方草稿。</param>
        /// <param name="message">校验错误或采用结果。</param>
        /// <returns>是否成功采用。</returns>
        public bool TryApprove(SandboxRole role, SandboxPlanData draft, out string message)
        {
            message = ValidatePlan(role, draft);
            if (message.Length != 0) return false;
            SandboxPlanData adopted = draft.Copy();
            adopted.status = SandboxPlanStatus.Approved;
            if (role == SandboxRole.Red) session.redPlan = adopted;
            else session.bluePlan = adopted;
            AddRecord("本方演示建议已采用", "采用前端草稿；尚未形成服务端动作执行事实", role, false, true);
            message = "已采用本方演示建议，未执行实际仿真动作";
            return true;
        }

        /// <summary>驳回本方当前待确认建议，并继续阻止时间推进。</summary>
        /// <param name="role">当前角色。</param>
        /// <param name="version">用户查看时的建议版本。</param>
        /// <param name="message">处理结果。</param>
        /// <returns>是否成功驳回。</returns>
        public bool TryReject(SandboxRole role, int version, out string message)
        {
            message = "主持人不能代替红蓝操作员处理建议";
            if (role != SandboxRole.Red && role != SandboxRole.Blue) return false;
            SandboxPlanData plan = GetPlan(role);
            if (plan.version != version || plan.status != SandboxPlanStatus.Pending)
            {
                message = "建议已处理或版本已变化，请重新查看";
                return false;
            }
            plan.status = SandboxPlanStatus.Rejected;
            AddRecord("本方演示建议已驳回", "建议未执行；未自动生成新的 AI 方案", role, false, true);
            message = "已驳回，等待后续建议";
            return true;
        }

        /// <summary>显式重新载入一份演示建议，不冒充模型重新生成。</summary>
        /// <param name="role">请求载入的本方角色。</param>
        /// <param name="message">操作说明。</param>
        /// <returns>是否载入。</returns>
        public bool TryReloadDemoPlan(SandboxRole role, out string message)
        {
            message = "请由本方操作员载入演示建议";
            if (role != SandboxRole.Red && role != SandboxRole.Blue || session.step >= 60) return false;
            SandboxPlanData plan = GetPlan(role);
            plan.version++;
            plan.status = SandboxPlanStatus.Pending;
            AddRecord("重新载入演示建议", "固定样例已载入，不是实时模型输出", role, false, true);
            message = "已重新载入本方固定演示建议";
            return true;
        }

        /// <summary>登记经预览的前端事件；不自行扣分或改变单位能力。</summary>
        /// <param name="role">请求角色。</param>
        /// <param name="eventType">四类事件之一。</param>
        /// <param name="targetId">受影响单位标识。</param>
        /// <param name="expectedRevision">生成预览时的状态修订。</param>
        /// <param name="message">拒绝原因或登记结果。</param>
        /// <returns>是否成功登记。</returns>
        public bool TryRegisterEvent(SandboxRole role, string eventType, string targetId, int expectedRevision, out string message)
        {
            message = "仅主持人可登记事件";
            if (role != SandboxRole.Host) return false;
            if (session.step >= 60) { message = "截止步后不能登记事件"; return false; }
            if (expectedRevision != session.revision) { message = "状态已更新，请重新预览事件"; return false; }
            SandboxUnitData target = session.units.FirstOrDefault(unit => unit.id == targetId);
            if (!EventTypes.Contains(eventType) || target == null) { message = "事件类型或目标无效"; return false; }
            AddRecord(eventType + "已登记", target.id + " · 仅记录前端事件，效果待服务端结算", target.side, eventType == "天气变化", false);
            message = "事件已登记；未执行正式规则结算";
            return true;
        }

        /// <summary>恢复经过校验的独立快照；播放和相机状态由客户端另行清理。</summary>
        /// <param name="data">待恢复状态。</param>
        /// <param name="message">校验结果。</param>
        /// <returns>是否已替换当前演示状态。</returns>
        public bool TryRestore(SandboxSessionData data, out string message)
        {
            message = ValidateSession(data);
            if (message.Length != 0) return false;
            session = CopySession(data);
            message = "已恢复本地演示快照，保持暂停";
            return true;
        }

        /// <summary>校验本方建议的版本、权限、资源额度和必填分工。</summary>
        /// <param name="role">处理角色。</param>
        /// <param name="draft">待校验草稿。</param>
        /// <returns>空字符串表示可采用。</returns>
        public string ValidatePlan(SandboxRole role, SandboxPlanData draft)
        {
            if (role != SandboxRole.Red && role != SandboxRole.Blue || draft == null || role != draft.side) return "只能处理本方建议";
            SandboxPlanData current = GetPlan(role);
            if (session.step >= 60) return "演示已到截止步";
            if (draft.version != current.version || current.status != SandboxPlanStatus.Pending) return "建议已处理或版本已变化";
            if (draft.resourceAllowance < 0 || draft.resourceAllowance > 20) return "资源额度须为 0 至 20 的整数";
            if (!ValidText(draft.priority, 120) || !ValidText(draft.reconAssignment, 120)
                || !ValidText(draft.groundAssignment, 120) || !ValidText(draft.supportAssignment, 120))
                return "优先级与三兵种分工均须填写 1 至 120 字";
            return string.Empty;
        }

        /// <summary>校验存档版本、完整身份、数值和交叉引用。</summary>
        /// <param name="data">未知来源的反序列化状态。</param>
        /// <returns>空字符串表示当前格式有效。</returns>
        public static string ValidateSession(SandboxSessionData data)
        {
            if (data == null || data.schemaVersion != 1) return "不支持的前端存档版本";
            if (!Guid.TryParse(data.sessionId, out _) || !ValidText(data.title, 60) || !TaskTypes.Contains(data.taskType)) return "存档标识或任务字段无效";
            if (data.step < 0 || data.step > 60 || data.revision < 1 || data.units == null || data.records == null) return "存档状态不完整";
            if (data.units.Length > MaxArmyUnits) return "红蓝双方地图总计最多 10 个单位，存档超出当前部署上限";
            var identities = new HashSet<string>();
            foreach (SandboxUnitData unit in data.units)
            {
                if (unit == null || !ValidText(unit.id, 40) || !identities.Add(unit.id)
                    || !ValidArmyIdentity(unit)
                    || !ValidText(unit.name, 60) || !ValidText(unit.status, 60)
                    || float.IsNaN(unit.x) || float.IsInfinity(unit.x) || unit.x < 0 || unit.x > 120
                    || float.IsNaN(unit.z) || float.IsInfinity(unit.z) || unit.z < 0 || unit.z > 80)
                    return "存档单位身份或坐标无效";
            }
            foreach (SandboxRole side in new[] { SandboxRole.Red, SandboxRole.Blue })
            {
                SandboxPlanData plan = side == SandboxRole.Red ? data.redPlan : data.bluePlan;
                if (plan == null || plan.side != side || plan.version < 1 || !Enum.IsDefined(typeof(SandboxPlanStatus), plan.status)
                    || plan.resourceAllowance < 0 || plan.resourceAllowance > 20 || !ValidText(plan.priority, 120)
                    || !ValidText(plan.reconAssignment, 120) || !ValidText(plan.groundAssignment, 120) || !ValidText(plan.supportAssignment, 120))
                    return "存档建议字段无效";
            }
            var recordIds = new HashSet<string>();
            if (data.records.Any(record => record == null || !ValidText(record.id, 80) || !recordIds.Add(record.id) || record.step < 0 || record.step > data.step
                || !Enum.IsDefined(typeof(SandboxRole), record.side) || !ValidText(record.title, 120) || !ValidText(record.detail, 500))) return "存档时间线字段无效";
            return string.Empty;
        }

        /// <summary>生成当前双方尚未采用的建议摘要。</summary>
        /// <returns>只包含等待方名称的摘要。</returns>
        private string GetWaitingSummary()
        {
            var waiting = new List<string>();
            if (session.redPlan.status != SandboxPlanStatus.Approved) waiting.Add("红方");
            if (session.bluePlan.status != SandboxPlanStatus.Approved) waiting.Add("蓝方");
            return string.Join("、", waiting);
        }

        /// <summary>按阵营读取内部建议。</summary>
        /// <param name="side">红方或蓝方。</param>
        /// <returns>内部建议对象，仅供适配器修改。</returns>
        private SandboxPlanData GetPlan(SandboxRole side) => side == SandboxRole.Red ? session.redPlan : session.bluePlan;

        /// <summary>追加一条带所属方和修订的操作记录。</summary>
        /// <param name="title">记录标题。</param>
        /// <param name="detail">事实说明。</param>
        /// <param name="side">所属阵营。</param>
        /// <param name="isPublic">是否公开。</param>
        /// <param name="privatePlan">是否为私有建议记录。</param>
        private void AddRecord(string title, string detail, SandboxRole side, bool isPublic, bool privatePlan)
        {
            session.revision++;
            session.records.Add(new SandboxRecord { id = Guid.NewGuid().ToString("N"), step = session.step, title = title, detail = detail, side = side, isPublic = isPublic, isPrivatePlan = privatePlan });
        }

        /// <summary>核对文本的非空与长度约束。</summary>
        /// <param name="value">待校验文本。</param>
        /// <param name="maximum">最大字符数。</param>
        /// <returns>是否满足约束。</returns>
        private static bool ValidText(string value, int maximum) => !string.IsNullOrWhiteSpace(value) && value.Length <= maximum;

        /// <summary>复制完整演示状态，隔离投影、存档与恢复后的可写引用。</summary>
        /// <param name="data">已校验的完整状态。</param>
        /// <returns>没有共享数组、建议和记录的副本。</returns>
        private static SandboxSessionData CopySession(SandboxSessionData data)
        {
            return new SandboxSessionData
            {
                schemaVersion = data.schemaVersion, sessionId = data.sessionId, title = data.title,
                step = data.step, revision = data.revision, taskType = data.taskType,
                units = data.units.Select(unit => unit.Copy()).ToArray(), redPlan = data.redPlan.Copy(), bluePlan = data.bluePlan.Copy(),
                records = data.records.Select(record => record.Copy()).ToList()
            };
        }

        /// <summary>建立固定的阶段一视觉验收样例。</summary>
        /// <returns>与旧页面原型代码无共享状态的新演示数据。</returns>
        private static SandboxSessionData CreateInitialSession()
        {
            Func<SandboxRole, SandboxPlanData> createPlan = side => new SandboxPlanData
            {
                side = side, status = SandboxPlanStatus.Pending, priority = "先检查中央桥梁，再前往对岸检查点",
                reconAssignment = "观察前方区域并报告已发现信息", groundAssignment = "沿已选路线前往中央桥梁", supportAssignment = "集结点待命，准备补给与运输"
            };
            return new SandboxSessionData
            {
                sessionId = Guid.NewGuid().ToString(), title = "青岚河谷 · 协同巡查", step = 18, taskType = TaskTypes[0],
                units = new[]
                {
                    new SandboxUnitData { id = "R-U01", name = "侦察无人机 01", side = SandboxRole.Red, type = SandboxUnitType.Recon, x = 25, z = 44, status = "观察待命" },
                    new SandboxUnitData { id = "R-G01", name = "陆兵单元 01", side = SandboxRole.Red, type = SandboxUnitType.Ground, x = 30, z = 40, status = "任务待命" },
                    new SandboxUnitData { id = "R-S01", name = "保障单元 01", side = SandboxRole.Red, type = SandboxUnitType.Support, x = 20, z = 36, status = "保障待命" },
                    new SandboxUnitData { id = "B-U01", name = "侦察无人机 01", side = SandboxRole.Blue, type = SandboxUnitType.Recon, x = 95, z = 36, status = "观察待命" },
                    new SandboxUnitData { id = "B-G01", name = "陆兵单元 01", side = SandboxRole.Blue, type = SandboxUnitType.Ground, x = 90, z = 40, status = "任务待命" },
                    new SandboxUnitData { id = "B-S01", name = "保障单元 01", side = SandboxRole.Blue, type = SandboxUnitType.Support, x = 100, z = 44, status = "保障待命" }
                },
                redPlan = createPlan(SandboxRole.Red), bluePlan = createPlan(SandboxRole.Blue),
                records = new List<SandboxRecord>
                {
                    new SandboxRecord { id = "initial", step = 0, title = "载入青岚河谷演示场景", detail = "双方各三个虚拟单位；所有状态均为固定样例", isPublic = true },
                    new SandboxRecord { id = "waiting", step = 18, title = "等待双方处理首次演示建议", detail = "关键建议未处理时，单步和播放共同阻塞", isPublic = true }
                }
            };
        }
    }
}
