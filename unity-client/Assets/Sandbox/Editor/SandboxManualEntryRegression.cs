namespace Qiongtu.Sandbox.Editor
{
    public static class SandboxManualEntryRegression
    {
        public static void Run()
        {
            SandboxManualSetupChecks.Run();
            SandboxBattleEntryChecks.Run();
            SandboxManualBattleChecks.Run();
            SandboxRoundReplayChecks.Run();
            SandboxRecordedEffectsChecks.Run();
            SandboxSelectedUnitChecks.Run();
            SandboxDeploymentBannerChecks.Run();
        }
    }
}
