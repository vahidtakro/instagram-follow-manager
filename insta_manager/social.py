"""Human-paced feed / reel engagement and story watching helpers.

All DOM reads and clicks happen inside execute_script so Instagram's React
re-renders cannot invalidate element references between Selenium commands.
"""

from __future__ import annotations

import random

from selenium.common.exceptions import WebDriverException

JS_FEED_POSTS = r"""
return (function(){
  function vis(e){var s=getComputedStyle(e);return e.getClientRects().length>0&&
    s.visibility!=='hidden'&&s.display!=='none'&&parseFloat(s.opacity)>0;}
  var out=[];
  var arts=document.querySelectorAll('article');
  for(var i=0;i<arts.length;i++){
    var a=arts[i]; if(!vis(a)) continue;
    var author=null, postId=null;
    var links=a.querySelectorAll('a[href]');
    for(var j=0;j<links.length;j++){
      var h=links[j].getAttribute('href')||'';
      if(!author && /^\/[A-Za-z0-9._]+\/?$/.test(h)) author=h.replace(/^\/|\/$/g,'');
      if(!postId && /^\/p\/[^/]+\//.test(h)) postId=h.split('/')[2];
    }
    var like=a.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']");
    var kind='post';
    if(a.querySelector('video')) kind='video';
    out.push({index:i, author:author, postId:postId, kind:kind,
              liked:!!like && like.getAttribute('aria-label')==='Unlike'});
  }
  return out;
})()
"""

JS_CLICK_ARTICLE_LIKE = r"""
return (function(){
  var a=document.querySelectorAll('article')[INDEX];
  if(!a) return null;
  var svg=a.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']");
  if(!svg) return null;
  var before=svg.getAttribute('aria-label');
  var btn=svg.closest('button,[role="button"]')||svg.parentElement;
  btn.click();
  return before;
})()
"""

JS_ARTICLE_LIKE_STATE = r"""
return (function(){
  var a=document.querySelectorAll('article')[INDEX];
  if(!a) return null;
  var svg=a.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']");
  return svg?svg.getAttribute('aria-label'):null;
})()
"""

JS_ACTIVE_REEL = r"""
return (function(){
  function vis(e){var s=getComputedStyle(e);return e.getClientRects().length>0&&
    s.visibility!=='hidden'&&s.display!=='none'&&parseFloat(s.opacity)>0;}
  function center(e){var r=e.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};}
  var videos=Array.from(document.querySelectorAll('video')).filter(vis);
  if(!videos.length) return null;
  var cx=innerWidth/2, cy=innerHeight/2;
  videos.sort(function(a,b){var ca=center(a),cb=center(b);
    return Math.pow(ca.x-cx,2)+Math.pow(ca.y-cy,2)-Math.pow(cb.x-cx,2)-Math.pow(cb.y-cy,2);});
  var v=videos[0];
  var scope=v;
  while(scope && !scope.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']"))
    scope=scope.parentElement;
  if(!scope) return null;
  var author=null;
  var links=scope.querySelectorAll("a[aria-label*=' reels'],a[href*='/reels/']");
  for(var i=0;i<links.length;i++){
    var label=links[i].getAttribute('aria-label')||'';
    var href=links[i].getAttribute('href')||'';
    var m=label.match(/^([A-Za-z0-9._]+)\s+reels?$/i);
    if(m){author=m[1];break;}
    var hm=href.match(/\/([A-Za-z0-9._]+)\/reels\//);
    if(hm){author=hm[1];break;}
  }
  var svg=scope.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']");
  return {author:author, liked:!!svg && svg.getAttribute('aria-label')==='Unlike'};
})()
"""

JS_CLICK_REEL_LIKE = r"""
return (function(){
  function vis(e){var s=getComputedStyle(e);return e.getClientRects().length>0&&s.visibility!=='hidden'&&s.display!=='none';}
  function center(e){var r=e.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};}
  var videos=Array.from(document.querySelectorAll('video')).filter(vis);
  if(!videos.length) return null;
  var cx=innerWidth/2, cy=innerHeight/2;
  videos.sort(function(a,b){var ca=center(a),cb=center(b);
    return Math.pow(ca.x-cx,2)+Math.pow(ca.y-cy,2)-Math.pow(cb.x-cx,2)-Math.pow(cb.y-cy,2);});
  var scope=videos[0];
  while(scope && !scope.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']"))
    scope=scope.parentElement;
  if(!scope) return null;
  var svg=scope.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']");
  if(!svg) return null;
  var before=svg.getAttribute('aria-label');
  (svg.closest('button,[role="button"]')||svg.parentElement).click();
  return before;
})()
"""

JS_REEL_LIKE_STATE = r"""
return (function(){
  function vis(e){var s=getComputedStyle(e);return e.getClientRects().length>0&&s.visibility!=='hidden'&&s.display!=='none';}
  function center(e){var r=e.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};}
  var videos=Array.from(document.querySelectorAll('video')).filter(vis);
  if(!videos.length) return null;
  var cx=innerWidth/2, cy=innerHeight/2;
  videos.sort(function(a,b){var ca=center(a),cb=center(b);
    return Math.pow(ca.x-cx,2)+Math.pow(ca.y-cy,2)-Math.pow(cb.x-cx,2)-Math.pow(cb.y-cy,2);});
  var scope=videos[0];
  while(scope && !scope.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']"))
    scope=scope.parentElement;
  var svg=scope?scope.querySelector("svg[aria-label='Like'],svg[aria-label='Unlike']"):null;
  return svg?svg.getAttribute('aria-label'):null;
})()
"""

JS_NEXT_REEL = r"""
return (function(){
  var e=document.querySelector("[role='button'][aria-label='Navigate to next Reel']");
  if(!e) return false;
  e.click(); return true;
})()
"""

JS_STORY_USERS = r"""
return (function(){
  var out=[];
  var els=document.querySelectorAll("[role='button'][aria-label^='Story by']");
  for(var i=0;i<els.length;i++){
    var label=els[i].getAttribute('aria-label')||'';
    var m=label.match(/Story by ([^,]+)/);
    if(m && m[1].trim()) out.push(m[1].trim());
  }
  return out.filter(function(v,i,a){return a.indexOf(v)===i;});
})()
"""

JS_OPEN_STORY = r"""
return (function(){
  var els=document.querySelectorAll("[role='button'][aria-label^='Story by']");
  for(var i=0;i<els.length;i++){
    if((els[i].getAttribute('aria-label')||'').indexOf('USERNAME')!==-1){els[i].click();return true;}
  }
  return false;
})()
"""

JS_STORY_OPEN = r"""
return (function(){
  return /\/stories\//.test(location.pathname) &&
         !!document.querySelector("[role='img'][aria-label='Close'], button[aria-label='Close']");
})()
"""

JS_STORY_AUTHOR = r"""
return (function(){var m=location.pathname.match(/^\/stories\/([^/]+)/);return m?m[1]:null;})()
"""

JS_STORY_NEXT = r"""
return (function(){
  var e=document.querySelector("[role='img'][aria-label='Next'],button[aria-label='Next']");
  if(!e) return false;
  (e.closest('[role="button"]')||e).click(); return true;
})()
"""

JS_STORY_CLOSE = r"""
return (function(){
  var e=document.querySelector("[role='img'][aria-label='Close'],button[aria-label='Close']");
  if(!e) return false;
  (e.closest('[role="button"]')||e).click(); return true;
})()
"""


def _js(driver, script: str, default=None):
    try:
        return driver.execute_script(script)
    except WebDriverException:
        return default


def feed_posts(driver) -> list[dict]:
    return _js(driver, JS_FEED_POSTS, []) or []


def click_article_like(driver, index: int) -> str | None:
    return _js(driver, JS_CLICK_ARTICLE_LIKE.replace("INDEX", str(int(index))))


def article_like_state(driver, index: int) -> str | None:
    return _js(driver, JS_ARTICLE_LIKE_STATE.replace("INDEX", str(int(index))))


def active_reel(driver) -> dict | None:
    return _js(driver, JS_ACTIVE_REEL)


def click_reel_like(driver) -> str | None:
    return _js(driver, JS_CLICK_REEL_LIKE)


def reel_like_state(driver) -> str | None:
    return _js(driver, JS_REEL_LIKE_STATE)


def next_reel(driver) -> bool:
    return bool(_js(driver, JS_NEXT_REEL, False))


def story_users(driver) -> list[str]:
    return _js(driver, JS_STORY_USERS, []) or []


def open_story(driver, username: str) -> bool:
    script = JS_OPEN_STORY.replace("USERNAME", username.replace("'", "\\'"))
    return bool(_js(driver, script, False))


def story_open(driver) -> bool:
    return bool(_js(driver, JS_STORY_OPEN, False))


def story_author(driver) -> str | None:
    return _js(driver, JS_STORY_AUTHOR)


def story_next(driver) -> bool:
    return bool(_js(driver, JS_STORY_NEXT, False))


def story_close(driver) -> bool:
    return bool(_js(driver, JS_STORY_CLOSE, False))


def wander_mouse(driver, steps: int | None = None) -> None:
    """Small irregular pointer movements so activity isn't perfectly static."""
    try:
        from selenium.webdriver.common.action_chains import ActionChains

        steps = steps if steps is not None else random.randint(2, 5)
        action = ActionChains(driver)
        for _ in range(steps):
            action.move_by_offset(random.randint(-90, 90), random.randint(-70, 70))
        action.perform()
    except Exception:
        pass


def scroll_feed(driver) -> None:
    """Scroll a variable amount, occasionally nudging back slightly."""
    try:
        amount = random.randint(420, 900)
        if random.random() < 0.15:
            amount = -random.randint(120, 260)
        driver.execute_script(f"window.scrollBy({{top:{amount},left:0,behavior:'smooth'}});")
    except WebDriverException:
        pass
