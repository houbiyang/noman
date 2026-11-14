using System;

namespace Qiongtu.Sandbox
{
    /// <summary>只消费完整确认步的观看游标，计算进度与观看进度分离。</summary>
    public sealed class SandboxFivepointPlayback
    {
        private string branch;
        private bool started, stalled;
        private double nextBoundary = double.PositiveInfinity;
        private int pendingStep = -1;
        public int PlayedStep { get; private set; }
        public double Rate { get; private set; }

        /// <summary>在新分支清空旧观看进度，禁止继承旧分支未来画面。</summary>
        /// <param name="branchId">当前有效分支标识。</param>
        /// <param name="originStep">恢复分支的最后完整步，初始对局为零。</param>
        public void Reset(string branchId, int originStep = 0)
        {
            branch = branchId; PlayedStep = originStep; started = originStep > 0; stalled = originStep > 0;
            Rate = 0; nextBoundary = double.PositiveInfinity;
            pendingStep = -1;
        }

        /// <summary>决定下一个可显示完整步，倍速只在五秒播放步起点确定。</summary>
        /// <param name="authority">当前已提交权威视图。</param>
        /// <param name="now">本机单调现实时间。</param>
        /// <returns>本次应显示的完整步编号。</returns>
        public int TargetStep(SandboxServerView authority, double now)
        {
            if (branch != authority.context.branch_id) Reset(authority.context.branch_id, authority.fivepoint.playback_origin_step);
            if (pendingStep >= 0) return pendingStep;
            int available = authority.fivepoint.step;
            bool finished = authority.fivepoint.result != null && !string.IsNullOrEmpty(authority.fivepoint.result.reason);
            int buffer = (available - PlayedStep) * 5;
            if (!started && buffer < 180 && !finished) return PlayedStep;
            if (stalled && buffer < 120 && !finished) return PlayedStep;
            started = true; stalled = false;
            if (double.IsPositiveInfinity(nextBoundary)) Schedule(buffer, now);
            if (now >= nextBoundary && PlayedStep < available)
            {
                pendingStep = PlayedStep + 1;
                return pendingStep;
            }
            return PlayedStep;
        }

        /// <summary>只有请求成功且帧校验通过后才推进游标，失败重试不得跳过完整步。</summary>
        /// <param name="branchId">已校验帧所属分支。</param>
        /// <param name="step">已显示的完整步。</param>
        /// <param name="availableStep">当前权威提交步。</param>
        /// <param name="now">帧确认时的单调现实时间。</param>
        public void ConfirmFrame(string branchId, int step, int availableStep, double now)
        {
            if (branchId != branch || step != pendingStep) return;
            PlayedStep = step;
            pendingStep = -1;
            Schedule((availableStep - PlayedStep) * 5, now);
        }

        /// <summary>固定本播放步速度，缓存耗尽时进入补缓冲状态。</summary>
        /// <param name="buffer">剩余已确认仿真秒。</param>
        /// <param name="now">当前现实时间。</param>
        private void Schedule(int buffer, double now)
        {
            Rate = buffer >= 120 ? 2 : buffer >= 60 ? 1 : buffer > 0 ? 0.5 : 0;
            stalled = buffer == 0;
            nextBoundary = Rate > 0 ? now + 5 / Rate : double.PositiveInfinity;
        }
    }
}
