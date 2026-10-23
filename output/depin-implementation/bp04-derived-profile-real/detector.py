import runpy, time, sys
from pathlib import Path
sys.path.insert(0, '/Users/anagha/projects/visn_rust/tools')
runpy.run_path('/Users/anagha/projects/visn_rust/tools/yolo26_runner.py', run_name='__main__')
Path('/Users/anagha/projects/visn_rust/output/depin-implementation/bp04-derived-profile-real/detector-paused').touch()
while not Path('/Users/anagha/projects/visn_rust/output/depin-implementation/bp04-derived-profile-real/detector-release').exists(): time.sleep(.05)
# changed while inference was pending
