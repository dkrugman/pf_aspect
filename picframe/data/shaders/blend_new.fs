// ----- boiler-plate code for fragment shader variable definition
#version 120
//precision highp float;

uniform sampler2D tex0;
uniform sampler2D tex1;
uniform sampler2D tex2;
uniform vec3 unib[5];
// see docstring Buffer
uniform vec3 unif[20];
// see docstring Shape

varying float dist;
varying float fog_start;
varying float is_3d;

//fragcolor

varying vec2 texcoordoutf;
varying vec2 texcoordoutb;

void main(void) {
  vec4 texf = texture2D(tex0, texcoordoutf);
  if (texcoordoutf[0] < 0.0 || texcoordoutf[0] > 1.0 ||
      texcoordoutf[1] < 0.0 || texcoordoutf[1] > 1.0) {
    texf.a = unif[15][2];
  }
  vec4 texb = texture2D(tex1, texcoordoutb);
  if (texcoordoutb[0] < 0.0 || texcoordoutb[0] > 1.0 ||
      texcoordoutb[1] < 0.0 || texcoordoutf[1] > 1.0) {
    texb.a = unif[15][2];
  }

  // blending
  float a = unif[14][2];
  gl_FragColor = vec4(1.0, 1.0, 0.0, 1.0);
  // simple fade //////////////////////////////////////////////////////
  if (unif[18][0] <= 0.0) {
    gl_FragColor = mix(texb, texf, a);
  } else if (unif[18][0] <= 1.0) {
    // burn /////////////////////////////////////////////////////////////
    a += 0.01;
    float y = 1.0 - smoothstep(a, a * 1.2, length(texf.rgb) * 0.577 + 0.01);
    gl_FragColor = mix(texb, texf * y, step(1.0, y));
  } else {
    // bump /////////////////////////////////////////////////////////////
    vec4 light = vec4(1.0, -1.0, -1.0, 1.0);
    float ffact = dot(light, texf);
    gl_FragColor = mix(texb * (1.0 + a * (ffact - 1.0)), texf, clamp(2.0 * a - 1.0, 0.0, 1.0));
  }
  gl_FragColor.rgb *= unif[18][1]; // brightness passed in Shape.unif[55] 0.0 to 1.0
}
