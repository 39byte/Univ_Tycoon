# asset

Blender 5.3로 절차적 생성한 PBR 리소스. 전부 `src/build_assets.py` 한 파일에서 재생성된다.

```
blender -b -P asset/src/build_assets.py              # 전체
blender -b -P asset/src/build_assets.py -- wpn_bow   # 하나만
```

각 모델은 절차적 셰이더를 텍스처 아틀라스(BaseColor+AO / Roughness / Metallic / Normal)로 베이크한 뒤 텍스처 내장 GLB로 내보낸다. 단위는 m, glTF 기준 +Y 위.

| 파일 | 노드 | 비고 |
|---|---|---|
| `models/snow_rocks.glb` | `rock_a` `rock_b` `rock_c` | 눈 덮인 바위 3종, 원점 = 바닥 중심 |
| `models/ice_crystals.glb` | `crystal_base` `crystal_ice` | 얼음 수정 군집 (높이 ~2.4 m) |
| `models/frost_pillar.glb` | `pillar` `pillar_crystal` | 육각 석주 3.3 m + 떠 있는 수정(게임에서 회전) |
| `models/snow_mountain.glb` | `mountain` | 원경 설산 22 m, 자유롭게 스케일 |
| `models/wpn_spear.glb` | `spear` | 원점 = 손잡이, 창끝 +Y |
| `models/wpn_hammer.glb` | `hammer` | 원점 = 손잡이, 머리 +Y |
| `models/wpn_staff.glb` | `staff` `staff_orb` | 원점 = 손잡이, 오브 발광 |
| `models/wpn_bow.glb` | `bow` | 원점 = 손잡이, 림 ±Y, 시위 -X |
| `models/wpn_flask.glb` | `flask` `flask_glass` `flask_liquid` | 원점 = 플라스크 몸통 중심 |
| `models/wpn_gauntlet.glb` | `gauntlet` `gauntlet_glow` | 오른손, 왼손은 X 미러. 주먹 +Z |
| `models/frost_golem.glb` | `hips` › `shoulderL/R` `hipL/R` … | `makeEnemy`와 같은 관절 위치(hips y=1.0). brute 1.65배, boss 2.05배 |
| `textures/snow_*.jpg` | color / normal / rough | 1024² 심리스, 외곽 설원 |
| `textures/ice_*.jpg` | color / normal / rough | 1024² 심리스, 아레나 바닥 |

## 캐릭터 (`src/build_chars.py`)

몸체·리그는 Quaternius **Universal Base Characters**, 동작은 **Universal Animation Library** (둘 다 CC0). 원본 zip은 `source/`에 두며 git에는 올리지 않는다(`.gitignore`).

```
blender -b -P asset/src/build_chars.py                 # anims + 학부 6명 + 서리 병사
blender -b -P asset/src/build_chars.py -- hero_eeu
```

| 파일 | 내용 |
|---|---|
| `models/hero_<학부>.glb` | sports·strat·eeu·interp·sci·eng. 의상은 몸체 메시를 본 가중치로 잘라 띄운 셸이라 같은 스켈레톤으로 변형됨 |
| `models/frost_soldier.glb` | 그런트·궁수용 서리 병사 |
| `models/anims.glb` | 스켈레톤 + 게임이 쓰는 19개 클립 (Idle, Jog, Sprint, Roll, Punch, Sword, Spell, Pistol, Hit, Death …) |

게임은 클립의 회전 트랙과 골반 위치만 쓰고, 골반 위치는 몸체별 기본 자세에 맞춰 보정한다. 무기는 손 본(`hand_r`/`hand_l`)에 붙인다.

`previews/`는 Cycles 확인용 렌더.

게임(`index.html`)은 시작 시 이 파일들을 불러온다. 브라우저는 `file://`에서 GLB를 못 읽으므로 로컬 서버로 열어야 한다(`python -m http.server` 후 `http://localhost:8000`). 로드에 실패한 에셋은 기존 툰 도형으로 자동 대체된다.

유리·얼음 재질은 `KHR_materials_transmission`을 쓴다. three.js는 투과 재질이 많으면 비싸므로, 아레나 외곽 수정처럼 수십 개를 깔 때는 `transmission`을 끄고 `opacity`로 대체할 것.
