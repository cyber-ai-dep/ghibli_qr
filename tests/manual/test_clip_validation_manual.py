"""
Manual test for clip_validation_service.py — standalone, not yet wired into the pipeline.

Downloads the same known-tricky images used in audit_failures.py (real single-person
photos previously flagged MULTIPLE_FACES, the aboy.jpg false NOT_REAL_PHOTO, Minecraft
renders, group photos) plus a couple of animal/cartoon/render sanity cases, and runs
them through clip_validation_service.validate_human_portrait() directly.

This hits the network (image downloads + first-run CLIP weight download, ~350MB) and
is not collected by the automated test suite (see pyproject.toml addopts).

Run from the repo root:
    PYTHONPATH=. .venv/bin/python tests/manual/test_clip_validation_manual.py
"""

import io
import sys
import time

import requests
from PIL import Image

from src.ghibli_portrait.services.clip_validation_service import validate_human_portrait

# (name, url, expectation) — expectation is the human-readable ground truth,
# not a code, since this script checks CLIP's ACCEPT/REJECT call, not exact codes.
#
# Ground-truth labels (4 buckets):
#   HUMAN              - single real person
#   HUMAN_MULTIFACES    - multiple real people/faces in frame
#   NOT_HUMAN           - no real human present (empty scene / synthetic render)
#   ANIMAL              - animal, no human
#
# Note: CLIP has no face detector/landmark model, so it cannot count faces.
# HUMAN_MULTIFACES here is ground truth for the test, not something CLIP is
# expected to detect on its own (see validate_human_portrait's ACCEPT/REJECT
# call) — actual face counting stays MediaPipe's job if this is integrated.
IMAGES = [
    # Real single-person photos the old MediaPipe heuristic flagged MULTIPLE_FACES on.
    # CLIP can't fix MULTIPLE_FACES (it doesn't count faces) but it SHOULD say "human".
    ("whitewoman2.jpg", "https://i.ibb.co/RkDPv6jf/whitewoman2.jpg", "HUMAN"),
    ("bear.jpg", "https://i.ibb.co/pjSfGq2Z/bear.jpg", "ANIMAL"),

    ("blackwoman1.jpg", "https://i.ibb.co/cKM1stSZ/blackwoman1.jpg", "HUMAN"),
    ("blackwoman2.jpg", "https://i.ibb.co/BKSG8VGc/blackwoman2.jpg", "HUMAN"),
    ("maskman2.jpg", "https://i.ibb.co/kgbXdcdQ/maskman2.jpg", "HUMAN"),
    # Real photo the old _is_synthetic_face heuristic false-positived on (NOT_REAL_PHOTO).
    ("aboy.jpg", "https://i.ibb.co/ym8RdLzS/aboy.jpg", "HUMAN"),

    # Synthetic renders the old heuristic correctly caught — CLIP should also reject.
    ("minecraft1.jpg", "https://i.ibb.co/b5nGjTHh/minecraft1.jpg", "NOT_HUMAN"),
    ("minecraft4.jpg", "https://i.ibb.co/zT7j7wM6/minecraft4.jpg", "NOT_HUMAN"),
    ("snake.jpg", "https://i.ibb.co/Mv48f9t/snake.jpg", "ANIMAL"),

    # Group photos — old heuristic correctly rejected via MULTIPLE_FACES.
    ("threemens.jpg", "https://i.ibb.co/7tXRgDyH/threemens.jpg", "HUMAN_MULTIFACES"),
    ("threewoman.jpg", "https://i.ibb.co/9HRCFNwq/threewoman.jpg", "HUMAN_MULTIFACES"),

    ("cat1.jpg", "https://i.ibb.co/1tLyRBzF/istockphoto-2158079666-612x612.jpg", "ANIMAL"),
    ("cat2.jpg", "https://i.ibb.co/N2CnTn1V/istockphoto-1361394182-612x612.jpg", "ANIMAL"),

    ("multi_facess.jpg", "https://i.ibb.co/tyDJhqH/threegirls.jpg", "HUMAN_MULTIFACES"),
    # Additional real-world batch — diverse skin tones, hijab/headscarf, studio and
    # candid photography styles. These are the images that matter most: the app's
    # own prompts already single out hijab/skin-tone fidelity as a sensitive case,
    # so a validator that disproportionately rejects these would be a real problem.
    ("black_wemon_portrait", "https://images.pexels.com/photos/17191688/pexels-photo-17191688.jpeg", "HUMAN"),
    ("indian_wemon", "https://images.pexels.com/photos/36383643/pexels-photo-36383643.jpeg", "HUMAN"),
    ("man_face_dark_Part_light_part", "https://media.istockphoto.com/id/2166802740/photo/confident-businessman-smiling-in-sunlit-urban-environment.jpg?b=1&s=612x612&w=0&k=20&c=mY22TWcBedLCzlfeboW8RIZdS44V13qTC7wlkMcEJWw=", "HUMAN"),
    ("gray_man_pic", "https://images.pexels.com/photos/325685/pexels-photo-325685.jpeg", "HUMAN"),
    ("hijab_girl_chines", "https://images.pexels.com/photos/11719178/pexels-photo-11719178.jpeg", "HUMAN"),
    ("hijab_girl_black", "https://images.pexels.com/photos/29118567/pexels-photo-29118567.jpeg", "HUMAN"),
    ("hijab_girl_green", "https://images.pexels.com/photos/12771905/pexels-photo-12771905.jpeg", "HUMAN"),
    ("white_muslim_girl", "https://media.istockphoto.com/id/956842252/photo/portrait-of-a-confident-muslim-girl.jpg?b=1&s=612x612&w=0&k=20&c=0WYTuT2YbtXMQjnnUKjNOy2_G2AWP_Swpb-ErbDYxwg=", "HUMAN"),
    ("hijabigirl2_black_Beige", "https://i.ibb.co/FkhVnk7T/hijabigirl2.jpg", "HUMAN"),
    ("black_girl_orange_hijab", "https://images.pexels.com/photos/38101763/pexels-photo-38101763.jpeg", "HUMAN"),
    ("minecraft5.jpg", "https://i.ibb.co/tpv5PYdZ/minecraft2.jpg", "NOT_HUMAN"),
    ("minecraft66.jpg", "https://i.ibb.co/Y4T1CpGZ/minecraft3.jpg", "NOT_HUMAN"),
    ("two_faces_red_hijabs", "https://images.pexels.com/photos/16339418/pexels-photo-16339418.jpeg", "HUMAN_MULTIFACES"),
    ("three_black_faces", "https://images.pexels.com/photos/34673724/pexels-photo-34673724.jpeg", "HUMAN_MULTIFACES"),
    ("black_weman_black_hijab", "https://images.pexels.com/photos/34913247/pexels-photo-34913247.png", "HUMAN"),
    ("minecraft77.jpg", "https://i.ibb.co/gZ9jmkbP/minecraft5.jpg", "NOT_HUMAN"),
    ("black_wemon_color_torban", "https://images.pexels.com/photos/33762211/pexels-photo-33762211.jpeg", "HUMAN"),
    ("black_weman_red_hijab", "https://images.pexels.com/photos/36437007/pexels-photo-36437007.jpeg", "HUMAN"),
    ("black_man4", "https://images.pexels.com/photos/30124372/pexels-photo-30124372.jpeg", "HUMAN"),
    ("black_man3", "https://images.pexels.com/photos/5082975/pexels-photo-5082975.jpeg", "HUMAN"),
    ("black_man2_Withlight", "https://images.pexels.com/photos/15946547/pexels-photo-15946547.jpeg", "HUMAN"),
    ("black_man1", "https://images.pexels.com/photos/30509132/pexels-photo-30509132.jpeg", "HUMAN"),
    ("schoolgirl", "https://media.istockphoto.com/id/1345020578/photo/cheerful-caucasian-schoolgirl-teenager-pupil-student-smiling-with-toothy-smile-looking-at.jpg?b=1&s=612x612&w=0&k=20&c=jsQRZP9z4YCAUH8UBHZ3CPva7RllTuFrAUJLEOam8ZU=", "HUMAN"),
    ("black_little_girl", "https://media.istockphoto.com/id/1353379172/photo/cute-little-african-american-girl-looking-at-camera.jpg?b=1&s=612x612&w=0&k=20&c=3qahdCVthwy9Q1lCY96GQHh8DipUWt7H7fJaVaRXsFs=", "HUMAN"),
    ("minecraft88.jpg", "https://i.ibb.co/TMJP4fvZ/minecraft6.png", "NOT_HUMAN"),
    ("black_empty.jpg", "https://i.ibb.co/NdFf2S0v/balx.jpg", "NOT_HUMAN"),
    ("dog.jpg", "https://images.pexels.com/photos/37425878/pexels-photo-37425878.jpeg", "ANIMAL"),

    ("empty_room.jpg", "https://images.pexels.com/photos/36721148/pexels-photo-36721148.jpeg", "NOT_HUMAN"),
    ("boam.jpg", "https://images.pexels.com/photos/38393450/pexels-photo-38393450.jpeg", "ANIMAL"),
    ("dogs_and_boy_back.jpg", "https://images.pexels.com/photos/18201735/pexels-photo-18201735.jpeg", "ANIMAL"),
    ("girl88.jpg", "https://media.istockphoto.com/id/1292415309/photo/funny-girl-on-pink-background.jpg?b=1&s=612x612&w=0&k=20&c=JlHoVb19kUyBXoEwoen9ByqCm7SuS2xAWOmRAf3sbzs=", "HUMAN"),
    ("girl_sun_glasses.jpg", "https://images.pexels.com/photos/16499147/pexels-photo-16499147.jpeg", "HUMAN"),

    ("man888.jpg", "https://i.ibb.co/Fb34x2nd/nightman2.jpg", "HUMAN"),
    ("man_mask.jpg", "https://i.ibb.co/kgbXdcdQ/maskman2.jpg", "HUMAN"),
    ("man_mask_helmet.jpg", "https://i.ibb.co/r2F26p5C/maskman.jpg", "HUMAN"),
    ("girl_girl.jpg", "https://images.pexels.com/photos/36772615/pexels-photo-36772615.jpeg", "HUMAN"),

    ("little_girls_multifaces.jpg", "https://images.pexels.com/photos/6652280/pexels-photo-6652280.jpeg", "HUMAN_MULTIFACES"),
    ("little_girls_multifaces55.jpg", "https://images.pexels.com/photos/37731790/pexels-photo-37731790.jpeg", "HUMAN_MULTIFACES"),
    ("little_girls_multifaces66.jpg", "https://images.pexels.com/photos/33301532/pexels-photo-33301532.jpeg", "HUMAN_MULTIFACES"),
    ("mens_multifaces77.jpg", "https://images.pexels.com/photos/7654080/pexels-photo-7654080.jpeg", "HUMAN_MULTIFACES"),
    ("mens_multifaces88.jpg", "https://images.pexels.com/photos/6616121/pexels-photo-6616121.jpeg", "HUMAN_MULTIFACES"),
    ("mens_multifaces99.jpg", "https://images.pexels.com/photos/9775717/pexels-photo-9775717.jpeg", "HUMAN_MULTIFACES"),
    ("little_girls_multifaces.jpg", "https://images.pexels.com/photos/6616654/pexels-photo-6616654.jpeg", "HUMAN_MULTIFACES"),



    ("toy.jpg", "https://images.pexels.com/photos/37248189/pexels-photo-37248189.jpeg", "NOT_HUMAN"),
    ("cartoons1.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRbcRkRY1hPJqQNJI1_p-iMbupeSTdth2XDEjnjNUQodw&s=10", "NOT_HUMAN"),
    ("cartoons2.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSytMy6npTNNb2gjbt3fGSMLbGI_3-y4gYJoAIH-GGCkA&s=10", "NOT_HUMAN"),
    ("cartoons3.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRIMyASFWQqFq29pVnHl73HUgt_RlrQ_7oA5Kqaih9dug&s=10", "NOT_HUMAN"),
    ("cartoons4.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTnV3KiRSlgHCB2rzY7TK38_HrCijfJpYEG3kG4wDlIIw&s=10", "NOT_HUMAN"),
    ("cartoons5.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcR9DU2EaU34IhL85utAk5jqY9ZNmQkJh0S-GBWp2j4hVQ&s", "NOT_HUMAN"),
    ("cartoons6.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRdaloTV1kz8THN7ICW2X9PEGWQo_VaXnxY9mtQslGHcg&s=10", "NOT_HUMAN"),
    ("cartoons7.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQnNUXmlaY07z_KwbTuLauUleI_WhqSW02HFELkUn-nzA&s", "NOT_HUMAN"),
    ("cartoons8.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRujSY_vCWIm3tmPPpHXGGgpa1QaE5EStdov9sOlmVuSQ&s=10", "NOT_HUMAN"),
    ("cartoons9.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTCNEO2B1bzs0bxiWDGDOawPBVzgN4W3Qds51mEJV1UkQ&s=10", "NOT_HUMAN"),
    ("cartoons10.jpg", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTjCtzfojHe-ORf2kTCporw83HSkKhqhwAIWXDyicdzjQ&s", "NOT_HUMAN"),

    ("two_girls.jpg", "https://images.pexels.com/photos/10653951/pexels-photo-10653951.jpeg", "HUMAN_MULTIFACES"),
    ("two_hijab_girls333.jpg", "https://images.pexels.com/photos/30320089/pexels-photo-30320089.jpeg", "HUMAN_MULTIFACES"),
    ("weman_black_torban.jpg", "https://images.pexels.com/photos/37774602/pexels-photo-37774602.jpeg", "HUMAN"),
    ("girl_black_dress.jpg", "https://images.pexels.com/photos/35875014/pexels-photo-35875014.jpeg", "HUMAN"),
    ("two_blond_girl.jpg", "https://images.pexels.com/photos/4457131/pexels-photo-4457131.jpeg", "HUMAN_MULTIFACES"),
 


    ("part_of_face.jpg", "https://images.pexels.com/photos/13302093/pexels-photo-13302093.jpeg", "HUMAN"),
    ("pic_with_lights.jpg", "https://images.pexels.com/photos/6033759/pexels-photo-6033759.jpeg", "HUMAN"),
    ("multi_5555.jpg", "https://images.pexels.com/photos/9539807/pexels-photo-9539807.jpeg", "HUMAN_MULTIFACES"),
    ("multi_999.jpg", "https://images.pexels.com/photos/10145404/pexels-photo-10145404.jpeg", "HUMAN_MULTIFACES"),

    ("multifaces_boys.jpg", "https://images.pexels.com/photos/10057685/pexels-photo-10057685.jpeg", "HUMAN_MULTIFACES"),
    ("boy656.jpg", "https://images.pexels.com/photos/31197508/pexels-photo-31197508.jpeg", "HUMAN"),
    ("boy_and_animal.jpg", "https://images.pexels.com/photos/38443596/pexels-photo-38443596.png", "NOT_HUMAN"),
    
    
    ("black_boy96.jpg", "https://images.pexels.com/photos/30264344/pexels-photo-30264344.jpeg", "HUMAN"),
    ("black_boy852.jpg", "https://media.istockphoto.com/id/1042424400/photo/teenager-with-afro-hair-style.jpg?b=1&s=612x612&w=0&k=20&c=jZOmTgIv9O-MFzTJtignd3lDcOSn2tagl-VdG2yZsFU=", "HUMAN"),


    ("black_boy78.jpg", "https://media.istockphoto.com/id/524857205/photo/smile-just-because.jpg?b=1&s=612x612&w=0&k=20&c=AirYEWIOgglqE8C9EHYxxmwNsN5zmFB_G9QYFaYEMJE=", "HUMAN"),
    ("black_boy425.jpg", "https://images.pexels.com/photos/27603963/pexels-photo-27603963.jpeg", "HUMAN"),
    ("black_boy963.jpg", "https://images.pexels.com/photos/14640360/pexels-photo-14640360.jpeg", "HUMAN"),
    ("black_boy366.jpg", "https://images.pexels.com/photos/10575113/pexels-photo-10575113.jpeg", "HUMAN"),
    ("black_boy753.jpg", "https://images.pexels.com/photos/5497133/pexels-photo-5497133.jpeg", "HUMAN"),
    ("black_boy424.jpg", "https://images.pexels.com/photos/4546943/pexels-photo-4546943.jpeg", "HUMAN"),
    ("MULTI_black_boys.jpg", "https://images.pexels.com/photos/14059344/pexels-photo-14059344.jpeg", "HUMAN_MULTIFACES"),
    ("multi_black_boy96.jpg", "https://images.pexels.com/photos/33079717/pexels-photo-33079717.jpeg", "HUMAN_MULTIFACES"),


    ("mask&_boy.jpg", "https://images.pexels.com/photos/16362124/pexels-photo-16362124.jpeg", "HUMAN"),
    ("black_boy5225.jpg", "https://images.pexels.com/photos/29406440/pexels-photo-29406440.jpeg", "HUMAN"),


    ("black_brothers.jpg", "https://images.pexels.com/photos/34180455/pexels-photo-34180455.jpeg","HUMAN_MULTIFACES"),
    ("black_mens3333.jpg", "https://images.pexels.com/photos/30037379/pexels-photo-30037379.jpeg","HUMAN_MULTIFACES"),
    ("black_boy8536.jpg", "https://images.pexels.com/photos/20573229/pexels-photo-20573229.jpeg", "HUMAN"),
    ("grayImg_boy96.jpg", "https://images.pexels.com/photos/35130620/pexels-photo-35130620.jpeg", "HUMAN"),

    ("black_boy486.jpg", "https://images.pexels.com/photos/17651327/pexels-photo-17651327.jpeg", "HUMAN"),
    ("black_boy6895.jpg", "https://images.pexels.com/photos/14182906/pexels-photo-14182906.jpeg", "HUMAN"),
    ("black_boy2226.jpg", "https://images.pexels.com/photos/19418968/pexels-photo-19418968.jpeg", "HUMAN"),
    ("black_boy863574.jpg", "https://images.pexels.com/photos/36239121/pexels-photo-36239121.jpeg", "HUMAN"),
    ("black_boy48276.jpg", "https://images.pexels.com/photos/10575003/pexels-photo-10575003.jpeg", "HUMAN"),



    ("notHuman1.jpg", "https://images.pexels.com/photos/10347621/pexels-photo-10347621.jpeg", "NOT_HUMAN"),
    ("notHuman2.jpg", "https://media.istockphoto.com/id/1169976861/photo/black-screen-with-white-frame-isolated.jpg?b=1&s=612x612&w=0&k=20&c=YpCwEf5U80HEjx1zLqGNJn5uMSSzHNUQf99vXBCoEAc=", "NOT_HUMAN"),
    ("notHuman3.jpg", "https://media.istockphoto.com/id/931409022/photo/designed-film-background.jpg?b=1&s=612x612&w=0&k=20&c=HuHK595L20_vneeBpaKe8CYB2yoqjhL5V7CORHzVkYw=", "NOT_HUMAN"),
    ("notHuman4.jpg", "https://images.pexels.com/photos/53536/sun-hand-finger-light-53536.jpeg", "NOT_HUMAN"),
    ("notHuman5.jpg", "https://images.pexels.com/photos/31757397/pexels-photo-31757397.jpeg", "NOT_HUMAN"),
    ("notHuman6.jpg", "https://images.pexels.com/photos/14185892/pexels-photo-14185892.jpeg", "NOT_HUMAN"),
    ("notHuman7.jpg", "https://images.pexels.com/photos/31974564/pexels-photo-31974564.jpeg", "NOT_HUMAN"),
    ("notHuman8.jpg", "https://images.pexels.com/photos/10037213/pexels-photo-10037213.jpeg", "NOT_HUMAN"),
    ("notHuman9.jpg", "https://images.pexels.com/photos/30736679/pexels-photo-30736679.jpeg", "NOT_HUMAN"),
    ("notHuman10.jpg", "https://images.pexels.com/photos/31313336/pexels-photo-31313336.jpeg", "NOT_HUMAN"),
    ("notHuman11.jpg", "https://images.pexels.com/photos/37459166/pexels-photo-37459166.jpeg", "NOT_HUMAN"),
    ("animals12.jpg", "https://images.pexels.com/photos/8333318/pexels-photo-8333318.jpeg", "ANIMAL"),
    ("notHuman13.jpg", "https://images.pexels.com/photos/37250794/pexels-photo-37250794.jpeg", "NOT_HUMAN"),
    ("notHuman14.jpg", "https://images.pexels.com/photos/30396852/pexels-photo-30396852.jpeg", "NOT_HUMAN"),
    ("notHuman15.jpg", "https://images.pexels.com/photos/13231692/pexels-photo-13231692.jpeg", "NOT_HUMAN"),
    ("notHuman16.jpg", "https://images.pexels.com/photos/6484520/pexels-photo-6484520.jpeg", "NOT_HUMAN"),
    ("notHuman17.jpg", "https://images.pexels.com/photos/34167245/pexels-photo-34167245.jpeg", "NOT_HUMAN"),
    ("notHuman18.jpg", "https://images.pexels.com/photos/9822131/pexels-photo-9822131.jpeg", "NOT_HUMAN"),
    ("notHuman19.jpg", "https://images.pexels.com/photos/34531729/pexels-photo-34531729.jpeg", "NOT_HUMAN"),

    ("notHuman20.jpg", "https://images.pexels.com/photos/30251451/pexels-photo-30251451.jpeg", "NOT_HUMAN"),
    ("notHuman21.jpg", "https://images.pexels.com/photos/17691608/pexels-photo-17691608.jpeg", "NOT_HUMAN"),

    ("notHuman22.jpg", "https://images.pexels.com/photos/9920930/pexels-photo-9920930.jpeg", "NOT_HUMAN"),
    ("notHuman23.jpg", "https://images.pexels.com/photos/9981144/pexels-photo-9981144.jpeg", "NOT_HUMAN"),
    ("notHuman24.jpg", "https://images.pexels.com/photos/9470814/pexels-photo-9470814.jpeg", "NOT_HUMAN"),
    ("notHuman25.jpg", "https://images.pexels.com/photos/9822139/pexels-photo-9822139.jpeg", "NOT_HUMAN"),
    ("notHuman26.jpg", "https://images.pexels.com/photos/15082123/pexels-photo-15082123.jpeg", "NOT_HUMAN"),
    ("notHuman27.jpg", "https://images.pexels.com/photos/30184087/pexels-photo-30184087.jpeg", "NOT_HUMAN"),
    ("ManyHuman.jpg", "https://images.pexels.com/photos/30147204/pexels-photo-30147204.jpeg", "HUMAN_MULTIFACES"),
    ("notHuman28.jpg", "https://images.pexels.com/photos/31103741/pexels-photo-31103741.jpeg", "NOT_HUMAN"),
    ("notHuman29.jpg", "https://media.istockphoto.com/id/485665028/photo/abstract-film-texture-background.jpg?b=1&s=612x612&w=0&k=20&c=kK6WA0JwjarKSDerKnFVjsriSUQvTMLGWiPi85lqJ3w=", "NOT_HUMAN"),
    
    ("notHuman30_empty_room.jpg", "https://media.istockphoto.com/id/1990444472/photo/scandinavian-style-cozy-living-room-interior.jpg?b=1&s=612x612&w=0&k=20&c=SlUMBo5qEOpQ1dxD8KB1OfSvXBTAGPrJkMQ9gQaN7xk=", "NOT_HUMAN"),
    ("notHuman31_empty_room.jpg", "https://images.pexels.com/photos/19837082/pexels-photo-19837082.jpeg", "NOT_HUMAN"),
    ("notHuman32_empty_room.jpg", "https://images.pexels.com/photos/34906893/pexels-photo-34906893.jpeg", "NOT_HUMAN"),
    ("notHuman33_empty_room.jpg", "https://images.pexels.com/photos/31212256/pexels-photo-31212256.jpeg", "NOT_HUMAN"),
    
    ("animal1.jpg", "https://media.istockphoto.com/id/1049028724/photo/red-eyed-tree-frog-smile.jpg?b=1&s=612x612&w=0&k=20&c=vRTjfwdNEKJp1Flafe4_z8TBUKJ7OYxwTUJTSKBQlvY=", "ANIMAL"),
    ("animal2.jpg", "https://images.pexels.com/photos/35671120/pexels-photo-35671120.jpeg", "ANIMAL"),
    ("animal3.jpg", "https://images.pexels.com/photos/41315/africa-african-animal-cat-41315.jpeg", "ANIMAL"),
    ("animal4.jpg", "https://images.pexels.com/photos/3925823/pexels-photo-3925823.jpeg", "ANIMAL"),
    ("animal5.jpg", "https://media.istockphoto.com/id/1022874582/photo/surprised-ring-tailed-lemur-with-open-mouth-and-eyes-wide-open.jpg?b=1&s=612x612&w=0&k=20&c=v4aXDWWmMAPp6_i-N6kkrsP8m5ZgYGsaML9NxAxGSL0=", "ANIMAL"),
    ("animal6.jpg", "https://images.pexels.com/photos/19679334/pexels-photo-19679334.jpeg", "ANIMAL"),
    ("animal7.jpg", "https://images.pexels.com/photos/31967047/pexels-photo-31967047.jpeg", "ANIMAL"),
    ("animal8.jpg", "https://images.pexels.com/photos/33869567/pexels-photo-33869567.jpeg", "ANIMAL"),
    ("animal9.jpg", "https://media.istockphoto.com/id/1938542212/photo/stormy-emotions-of-fear-surprise-of-dog-with-open-mouth-bulging-eyes-sale-shock.jpg?b=1&s=612x612&w=0&k=20&c=iKL9A3XezpHSu9BmT_9hJMNLibZEvfXJkOvnGNoLpbs=", "ANIMAL"),
    

    
    
    

   
    


    ]


# Ground-truth expectation string -> the short `category` validate_human_portrait()
# actually returns, so we can flag mismatches without hand-parsing the label sentence.
_EXPECTED_TO_CATEGORY = {
    "HUMAN": "human",
    "HUMAN_MULTIFACES": "human_multifaces",
    "NOT_HUMAN": "not_human",
    "ANIMAL": "animals",
}

print(f"{'file':<20} {'expected':<20} {'category':<18} {'match':<7} {'ok':<6} {'code':<18} time")
print("-" * 135)

rows = []

for name, url, expected in IMAGES:
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "clip-manual-test/1.0"})
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"{name:<20} DOWNLOAD FAILED: {e}")
        rows.append({
            "file": name, "url": url, "expected": expected, "category": "", "match": "",
            "ok": "", "code": "", "label": "", "time_ms": "",
            "error": f"DOWNLOAD FAILED: {e}",
        })
        continue

    t0 = time.perf_counter()
    result = validate_human_portrait(img, image_url=name)
    dt = (time.perf_counter() - t0) * 1000

    expected_category = _EXPECTED_TO_CATEGORY.get(expected, expected.lower())
    match = expected_category == result.category

    print(f"{name:<20} {expected:<20} {result.category:<18} {str(match):<7} {str(result.ok):<6} {str(result.code):<18} {dt:.0f}ms")
    print(f"    label: {result.label}")
    print(f"    scores: { {k: round(v, 3) for k, v in result.scores.items()} }")

    row = {
        "file": name,
        "url": url,
        "expected": expected,
        "category": result.category,
        "match": match,
        "ok": result.ok,
        "code": result.code,
        "label": result.label,
        "time_ms": round(dt, 1),
        "error": result.error or "",
    }
    row.update({f"score__{k}": round(v, 4) for k, v in result.scores.items()})
    rows.append(row)

print("\nDone.")

# Save the full run as a CSV table for later analysis (Excel, pandas.read_csv, etc).
if rows:
    import csv as _csv
    from datetime import datetime as _datetime

    out_path = f"tests/manual/clip_validation_results_{_datetime.now():%Y%m%d_%H%M%S}.csv"
    fieldnames = list({k for row in rows for k in row})
    # Keep the core columns first, in a stable order; scores trail after.
    core = ["file", "expected", "category", "match", "ok", "code", "label", "time_ms", "error"]
    fieldnames = core + sorted(k for k in fieldnames if k not in core)

    with open(out_path, "w", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    mismatches = [r for r in rows if r["match"] is False]
    print(f"Saved {len(rows)} results to {out_path} ({len(mismatches)} mismatches)")

    # Separate summary of just the mismatches, same base name as the main CSV.
    mismatch_path = out_path.replace(".csv", "_mismatch_summary.csv")
    mismatch_fieldnames = ["image name", "image url", "expect", "model predict"]

    with open(mismatch_path, "w", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=mismatch_fieldnames)
        writer.writeheader()
        for r in mismatches:
            writer.writerow({
                "image name": r["file"],
                "image url": r["url"],
                "expect": r["expected"],
                "model predict": r["category"],
            })

    print(f"Saved {len(mismatches)} mismatches to {mismatch_path}")
