Shader "CannonRehearsal/Smoke" { Properties {_BaseColor("Color",Color)=(.2,.2,.2,.5)} SubShader { Tags {"RenderPipeline"="UniversalPipeline" "RenderType"="Transparent" "Queue"="Transparent"} Pass { Blend SrcAlpha OneMinusSrcAlpha ZWrite Off Cull Off HLSLPROGRAM
#pragma vertex vert
#pragma fragment frag
#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
struct A {float4 positionOS:POSITION;};struct V {float4 positionCS:SV_POSITION;};float4 _BaseColor;
V vert(A v){V o;o.positionCS=TransformObjectToHClip(v.positionOS.xyz);return o;} half4 frag(V v):SV_Target{return _BaseColor;}
ENDHLSL } } }
