using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>Adapted from the cannon-3d rehearsal. Mechanical pose/tint only, never combat logic.</summary>
    public sealed class SandboxCombatActor : MonoBehaviour
    {
        private Transform yaw, pitch, recoil, muzzle;
        private Vector3 recoilRest, restPosition;
        private Quaternion restRotation, yawRest, pitchRest;
        private Renderer[] meshes;
        private Color[] colors;
        private MaterialPropertyBlock block;
        private Vector3 aim;
        private float firedAt = -100, hitAt = -100, deadAt = -100;
        private bool aiming, dead, airborne;
        private bool recordedClock;
        private float recordedSeconds;
        private float Clock => recordedClock ? recordedSeconds : Time.time;
        /// <summary>只读回放直接采样绝对时间，暂停/拖动不依赖Time.time，也不执行任何战斗逻辑。</summary>
        public void SampleRecorded(float seconds, Vector3? target, float shotAt, float hitAt, float retiredAt, bool destroyed)
        {
            recordedClock = true; recordedSeconds = seconds;
            firedAt = shotAt; this.hitAt = hitAt; deadAt = retiredAt; dead = destroyed;
            aiming = target.HasValue; if (target.HasValue) aim = target.Value;
        }
        public bool Destroyed => dead;
        public Vector3 Muzzle => muzzle ? muzzle.position : transform.position + Vector3.up * 2;
        public Vector3 Center
        {
            get
            {
                if (meshes == null || meshes.Length == 0) return transform.position;
                Bounds bounds = meshes[0].bounds;
                foreach (var mesh in meshes) bounds.Encapsulate(mesh.bounds);
                return bounds.center;
            }
        }

        public void Configure(Color team, bool isAirborne)
        {
            yaw = transform.Find("TurretYaw");
            pitch = yaw ? yaw.Find("GunPitch") : null;
            recoil = pitch ? pitch.Find("GunRecoil") : null;
            muzzle = recoil ? recoil.Find("Muzzle") : null;
            if (yaw) yawRest = yaw.localRotation;
            if (pitch) pitchRest = pitch.localRotation;
            if (recoil) recoilRest = recoil.localPosition;
            restPosition = transform.localPosition; restRotation = transform.localRotation;
            meshes = GetComponentsInChildren<Renderer>(); colors = new Color[meshes.Length];
            for (int i = 0; i < meshes.Length; i++)
                colors[i] = meshes[i].sharedMaterial.name.StartsWith("TeamAccent") ? team : meshes[i].sharedMaterial.GetColor("_BaseColor");
            block = new MaterialPropertyBlock(); airborne = isAirborne;
        }

        public void Aim(Vector3 target) { aim = target; aiming = true; }
        public void Fire(Vector3 target) { Aim(target); firedAt = Time.time; }
        public void Hit(bool lethal) { hitAt = Time.time; if (lethal) SetDestroyed(true, true); }
        public void SetDestroyed(bool value, bool animate = false)
        {
            if (dead == value) return;
            dead = value; deadAt = Time.time - (animate ? 0 : 2);
            if (!dead) { transform.localPosition = restPosition; transform.localRotation = restRotation; }
        }
        public void SetAirborne(bool value) { airborne = value; }

        private void LateUpdate()
        {
            if (block == null) return;
            float shotAge = Clock - firedAt, hitAge = Clock - hitAt, deathAge = Clock - deadAt;
            // 跳回开火之前必须恢复炮塔及模型姿态；不能携带上一次seek留下的角度。
            if (recordedClock)
            {
                transform.localPosition = restPosition; transform.localRotation = restRotation;
                if (yaw) yaw.localRotation = yawRest;
                if (pitch) pitch.localRotation = pitchRest;
            }
            if (yaw && pitch && aiming && !dead)
            {
                Vector3 local = transform.InverseTransformPoint(aim) - yaw.localPosition;
                Quaternion bearing = Quaternion.Euler(0, Mathf.Atan2(local.x, local.z) * Mathf.Rad2Deg, 0);
                yaw.localRotation = recordedClock ? bearing : Quaternion.RotateTowards(yaw.localRotation, bearing, 540 * Time.deltaTime);
                Vector3 target = yaw.InverseTransformPoint(aim) - pitch.localPosition;
                float elevation = Mathf.Atan2(target.y, new Vector2(target.x, target.z).magnitude) * Mathf.Rad2Deg;
                pitch.localRotation = recordedClock ? Quaternion.Euler(-elevation, 0, 0) : Quaternion.RotateTowards(pitch.localRotation, Quaternion.Euler(-elevation, 0, 0), 360 * Time.deltaTime);
            }
            if (recoil) recoil.localPosition = recoilRest + Vector3.back * (.22f * Mathf.Clamp01(1 - shotAge / .28f));
            float flash = Mathf.Clamp01(1 - hitAge / .32f);
            for (int i = 0; i < meshes.Length; i++)
            {
                block.Clear(); block.SetColor("_BaseColor", Color.Lerp(dead ? colors[i] * .3f : colors[i], new Color(1, .65f, .26f), flash));
                meshes[i].SetPropertyBlock(block);
            }
            transform.localPosition = restPosition;
            transform.localRotation = restRotation;
            if (dead && airborne)
            {
                float fall = Mathf.Clamp01(deathAge / 1.5f);
                // Only the model falls. The authoritative unit coordinate is never moved by VFX.
                transform.position += Vector3.down * (SandboxUnitView.ReconAltitude * fall);
                transform.localRotation *= Quaternion.Euler(fall * 48, fall * 165, fall * 75);
            }
            else if (hitAge < .4f)
                transform.localRotation *= Quaternion.Euler(0, 0, Mathf.Sin(hitAge * 65) * 4 * (1 - hitAge / .4f));
        }
    }
}
